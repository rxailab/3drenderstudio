"""Measure a backdrop photograph once: its camera, the plane things stand on, the free part of that plane, its lamps.

    <depth python> scripts/backdrop_measure.py FOLDER

FOLDER holds backdrop.png. Written beside it: geometry.json, support.png (white where the plane is free) and depth.npy
(metric depth at every eighth pixel). blender/backdrop.py reads them every time a product is set down in the place;
this script runs once per backdrop, when scene_backdrop.py adds one.

This is the only part of the app that runs a depth model, and it runs in its own Python environment (.venv-depth, with
PyTorch and transformers), never in the app's. The model is Depth Pro (apple/DepthPro-hf): metric depth and the focal
length from one picture. Its weights are licensed for research use only (Apple's model licence): for commercial use,
measure the backdrops with a model whose licence allows it and write the same three files.

Camera axes: x right, y down, z forward (OpenCV).
"""
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageFilter
from transformers import DepthProForDepthEstimation, DepthProImageProcessor

MODEL = 'apple/DepthPro-hf'
STEP = 8                      # depth.npy keeps every eighth pixel: enough for the room's shape as a light


def depth_of(image):
    processor = DepthProImageProcessor.from_pretrained(MODEL)
    model = DepthProForDepthEstimation.from_pretrained(MODEL).eval()
    device = 'cuda' if torch.cuda.is_available() and os.environ.get('BACKDROP_DEVICE', '') != 'cpu' else 'cpu'
    inputs = processor(images=image, return_tensors='pt')
    try:
        model = model.to(device)
        with torch.no_grad():
            answer = model(**{key: value.to(device) for key, value in inputs.items()})
    except RuntimeError:                                         # the graphics card is busy rendering: the processor does it, slower
        if device == 'cpu': raise
        device = 'cpu'; model = model.to(device)
        with torch.no_grad():
            answer = model(**inputs)
    post = processor.post_process_depth_estimation(answer, target_sizes=[(image.height, image.width)])[0]
    return post['predicted_depth'].float().cpu().numpy().astype(np.float32), float(post['focal_length']), float(post['field_of_view']), device


def plane_of(cloud, seed=1):
    """The largest plane that faces up and lies below the camera: (normal towards the camera, camera height, pixels on it)."""
    sample = cloud[::8, ::8].reshape(-1, 3); sample = sample[np.isfinite(sample).all(1) & (sample[:, 2] > .05)]
    lower = sample[sample[:, 1] > 0]
    if len(lower) < 50:
        raise ValueError('no plane below the camera')
    rng = np.random.default_rng(seed); best = (0, None, None)
    for _ in range(800):
        a, b, c = lower[rng.choice(len(lower), 3, replace=False)]
        n = np.cross(b - a, c - a); length = np.linalg.norm(n)
        if length < 1e-9: continue
        n = n / length
        if n[1] > 0: n = -n                                      # towards the camera: up is -y
        if -n[1] < .5: continue                                  # a wall or a front, not something to stand on
        d = -n @ a                                               # n.P + d = 0 on the plane; d is the camera's height above it
        if d <= .05: continue
        count = int((np.abs(sample @ n + d) < .012 * sample[:, 2] + .004).sum())
        if count > best[0]: best = (count, n, d)
    _, n, d = best
    if n is None:
        raise ValueError('no plane to stand on was found')
    for _ in range(3):
        keep = np.abs(sample @ n + d) < .012 * sample[:, 2] + .004
        centre = sample[keep].mean(0); _, _, vt = np.linalg.svd(sample[keep] - centre, full_matrices=False)
        n = vt[2] if vt[2][1] < 0 else -vt[2]; d = -n @ centre
    return n, float(d), np.abs(cloud @ n + d) < .015 * cloud[..., 2] + .005


def facing(coarse, normal, shape):
    """Where the surface itself faces the way the plane does, from the coarse cloud (every STEP-th pixel). The front of a
    bench top lies within a finger's width of the top's plane all along its edge, but it faces the camera, not up."""
    across = coarse[1:-1, 2:] - coarse[1:-1, :-2]; down = coarse[2:, 1:-1] - coarse[:-2, 1:-1]
    n = np.cross(across, down); n /= np.maximum(np.linalg.norm(n, axis=2, keepdims=True), 1e-9)
    agree = np.zeros(coarse.shape[:2], bool); agree[1:-1, 1:-1] = np.abs(n @ normal) > .85
    whole = np.zeros(shape, bool); big = np.kron(agree, np.ones((STEP, STEP), bool))
    whole[:min(shape[0], big.shape[0]), :min(shape[1], big.shape[1])] = big[:shape[0], :shape[1]]
    return whole


def free_plane(inliers, coarse, normal):
    """The part of the plane a product can stand on: facing up, in one piece, a little in from its edge."""
    mask = one_piece(inliers & facing(coarse, normal, inliers.shape))
    reach = max(4, inliers.shape[1] // 200)
    return np.asarray(Image.fromarray((mask * 255).astype(np.uint8)).filter(ImageFilter.MinFilter(2 * reach + 1))) > 128


def one_piece(mask):
    """The largest connected part of the plane. A wall far behind crosses the plane's height too: nothing can stand there."""
    h, w = mask.shape; small = mask[:h - h % 8, :w - w % 8].reshape(h // 8, 8, w // 8, 8).mean((1, 3)) > .5
    labels = np.zeros(small.shape, int); sizes = {}; current = 0
    for y, x in zip(*np.nonzero(small)):
        if labels[y, x]: continue
        current += 1; stack = [(y, x)]; labels[y, x] = current; count = 0
        while stack:
            cy, cx = stack.pop(); count += 1
            for ny, nx in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1)):
                if 0 <= ny < small.shape[0] and 0 <= nx < small.shape[1] and small[ny, nx] and not labels[ny, nx]:
                    labels[ny, nx] = current; stack.append((ny, nx))
        sizes[current] = count
    if not sizes:
        return mask
    keep = np.kron(labels == max(sizes, key=sizes.get), np.ones((8, 8), bool))
    whole = np.zeros_like(mask); whole[:keep.shape[0], :keep.shape[1]] = keep
    return mask & whole


def lamps_of(rgb, cloud, support):
    """Lit fittings and windows in view: clipped, near-white areas. Each as a flat rectangle in space."""
    h, w = rgb.shape[:2]
    bright = (rgb.min(2) > .965) & ~support
    small = np.asarray(Image.fromarray((bright * 255).astype(np.uint8)).resize((w // 8, h // 8), Image.Resampling.BOX)) > 128
    labels = np.zeros(small.shape, int); found = []; current = 0
    for y, x in zip(*np.nonzero(small)):
        if labels[y, x]: continue
        current += 1; stack = [(y, x)]; labels[y, x] = current; cells = []
        while stack:
            cy, cx = stack.pop(); cells.append((cy, cx))
            for ny, nx in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1)):
                if 0 <= ny < small.shape[0] and 0 <= nx < small.shape[1] and small[ny, nx] and not labels[ny, nx]:
                    labels[ny, nx] = current; stack.append((ny, nx))
        if len(cells) < 12: continue
        ys, xs = np.array(cells).T * 8 + 4
        points = cloud[np.minimum(ys, h - 1), np.minimum(xs, w - 1)]; centre = np.median(points, 0)
        _, _, vt = np.linalg.svd(points - centre, full_matrices=False)
        reach = [(points - centre) @ vt[axis] for axis in (0, 1)]
        size = [float(np.percentile(r, 97) - np.percentile(r, 3)) for r in reach]
        found.append({'pixels': len(cells) * 64, 'box': [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())],
                      'centre': centre.tolist(), 'along': vt[0].tolist(), 'across': vt[1].tolist(), 'size': size})
    return sorted(found, key=lambda row: -row['pixels'])[:2]


def main():
    folder = Path(sys.argv[1]); started = time.time()
    image = Image.open(folder / 'backdrop.png').convert('RGB'); w, h = image.size
    torch.set_num_threads(max(4, (os.cpu_count() or 8) // 2))
    depth, focal, fov, device = depth_of(image)
    v, u = np.mgrid[0:h, 0:w].astype(np.float32)
    cloud = np.dstack([(u - w / 2) / focal * depth, (v - h / 2) / focal * depth, depth])
    normal, height, support = plane_of(cloud); support = free_plane(support, cloud[::STEP, ::STEP], normal)
    rgb = np.asarray(image).astype(np.float32) / 255
    report = {'version': 1, 'size': [w, h], 'focal_px': focal, 'fov_deg': fov, 'normal': [float(n) for n in normal], 'height_m': height,
              'tilt_down_deg': float(np.degrees(np.arcsin(np.clip(-normal[2], -1, 1)))), 'roll_deg': float(np.degrees(np.arctan2(normal[0], -normal[1]))),
              'support_share': float(support.mean()), 'depth_step': STEP, 'lamps': lamps_of(rgb, cloud, support),
              # how far the nearest free plane is: with the focal length, the size of product the picture is framed for
              'near_m': float(np.percentile(depth[support], 5)) if support.any() else 0.0,
              'measured_with': MODEL, 'device': device, 'seconds': round(time.time() - started, 1)}
    np.save(folder / 'depth.npy', depth[::STEP, ::STEP].astype(np.float16))
    Image.fromarray((support * 255).astype(np.uint8)).save(folder / 'support.png')
    (folder / 'geometry.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items() if key != 'lamps'}))


if __name__ == '__main__':
    main()
