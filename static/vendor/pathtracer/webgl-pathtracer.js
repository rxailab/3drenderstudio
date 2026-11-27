/*! three-gpu-pathtracer 0.0.28 + three-mesh-bvh 0.9.17 + Three.js r187 example utilities | MIT licenses in this directory. */
import {woodWaveGLSL} from '../../viewport-wood-wave.js';
var __defProp = Object.defineProperty;
var __export = (target2, all) => {
  for (var name in all)
    __defProp(target2, name, { get: all[name], enumerable: !0 });
};

// npm/three-gpu-pathtracer/src/core/WebGLPathTracer.js
import { PerspectiveCamera as PerspectiveCamera2, Scene, Vector2 as Vector29, Clock, NormalBlending as NormalBlending2, NoBlending as NoBlending4, AdditiveBlending } from "../three/build/three.module.js";

// npm/three-gpu-pathtracer/src/core/PathTracingSceneGenerator.js
import { BufferGeometry as BufferGeometry5 } from "../three/build/three.module.js";

// npm/three-mesh-bvh/src/core/BVH.js
import { Box3 as Box33 } from "../three/build/three.module.js";

// npm/three-mesh-bvh/src/core/Constants.js
var FLOAT32_EPSILON = Math.pow(2, -24), SKIP_GENERATION = Symbol("SKIP_GENERATION"), DEFAULT_OPTIONS = {
  strategy: 0,
  maxDepth: 40,
  targetLeafSize: 10,
  useSharedArrayBuffer: !1,
  setBoundingBox: !0,
  onProgress: null,
  indirect: !1,
  verbose: !0,
  range: null,
  [SKIP_GENERATION]: !1
};

// npm/three-mesh-bvh/src/utils/ArrayBoxUtilities.js
function arrayToBox(nodeIndex32, array, target2) {
  return target2.min.x = array[nodeIndex32], target2.min.y = array[nodeIndex32 + 1], target2.min.z = array[nodeIndex32 + 2], target2.max.x = array[nodeIndex32 + 3], target2.max.y = array[nodeIndex32 + 4], target2.max.z = array[nodeIndex32 + 5], target2;
}
function getLongestEdgeIndex(bounds) {
  let splitDimIdx = -1, splitDist = -1 / 0;
  for (let i = 0; i < 3; i++) {
    let dist = bounds[i + 3] - bounds[i];
    dist > splitDist && (splitDist = dist, splitDimIdx = i);
  }
  return splitDimIdx;
}
function expandByPrimitiveBounds(startIndex, primitiveBounds, bounds) {
  for (let d = 0; d < 3; d++) {
    let tCenter = primitiveBounds[startIndex + 2 * d], tHalf = primitiveBounds[startIndex + 2 * d + 1], tMin = tCenter - tHalf, tMax = tCenter + tHalf;
    tMin < bounds[d] && (bounds[d] = tMin), tMax > bounds[d + 3] && (bounds[d + 3] = tMax);
  }
}
function computeSurfaceArea(bounds) {
  let d0 = bounds[3] - bounds[0], d1 = bounds[4] - bounds[1], d2 = bounds[5] - bounds[2];
  return 2 * (d0 * d1 + d1 * d2 + d2 * d0);
}

// npm/three-mesh-bvh/src/core/utils/nodeBufferUtils.js
function IS_LEAF(n16, uint16Array) {
  return uint16Array[n16 + 15] === 65535;
}
function OFFSET(n32, uint32Array) {
  return uint32Array[n32 + 6];
}
function COUNT(n16, uint16Array) {
  return uint16Array[n16 + 14];
}
function LEFT_NODE(n32) {
  return n32 + 8;
}
function RIGHT_NODE(n32, uint32Array) {
  let relativeOffset = uint32Array[n32 + 6];
  return n32 + relativeOffset * 8;
}
function SPLIT_AXIS(n32, uint32Array) {
  return uint32Array[n32 + 7];
}

// npm/three-mesh-bvh/src/core/build/computeBoundsUtils.js
function getBounds(primitiveBounds, offset, count, target2, centroidTarget) {
  let minx = 1 / 0, miny = 1 / 0, minz = 1 / 0, maxx = -1 / 0, maxy = -1 / 0, maxz = -1 / 0, cminx = 1 / 0, cminy = 1 / 0, cminz = 1 / 0, cmaxx = -1 / 0, cmaxy = -1 / 0, cmaxz = -1 / 0, boundsOffset = primitiveBounds.offset || 0;
  for (let i = (offset - boundsOffset) * 6, end = (offset + count - boundsOffset) * 6; i < end; i += 6) {
    let cx = primitiveBounds[i + 0], hx = primitiveBounds[i + 1], lx = cx - hx, rx = cx + hx;
    lx < minx && (minx = lx), rx > maxx && (maxx = rx), cx < cminx && (cminx = cx), cx > cmaxx && (cmaxx = cx);
    let cy = primitiveBounds[i + 2], hy = primitiveBounds[i + 3], ly = cy - hy, ry = cy + hy;
    ly < miny && (miny = ly), ry > maxy && (maxy = ry), cy < cminy && (cminy = cy), cy > cmaxy && (cmaxy = cy);
    let cz = primitiveBounds[i + 4], hz = primitiveBounds[i + 5], lz = cz - hz, rz = cz + hz;
    lz < minz && (minz = lz), rz > maxz && (maxz = rz), cz < cminz && (cminz = cz), cz > cmaxz && (cmaxz = cz);
  }
  target2[0] = minx, target2[1] = miny, target2[2] = minz, target2[3] = maxx, target2[4] = maxy, target2[5] = maxz, centroidTarget[0] = cminx, centroidTarget[1] = cminy, centroidTarget[2] = cminz, centroidTarget[3] = cmaxx, centroidTarget[4] = cmaxy, centroidTarget[5] = cmaxz;
}

// npm/three-mesh-bvh/src/core/build/splitUtils.js
var BIN_COUNT = 8, SWEEP_COUNT = 8, binCounts = /* @__PURE__ */ new Int32Array(BIN_COUNT), binBounds = /* @__PURE__ */ new Float32Array(BIN_COUNT * 6), binRightBounds = /* @__PURE__ */ new Float32Array(BIN_COUNT * 6), leftBounds = /* @__PURE__ */ new Float32Array(6), rightBounds = /* @__PURE__ */ new Float32Array(6), sweepOrder = /* @__PURE__ */ new Int32Array(SWEEP_COUNT), sweepRightBounds = /* @__PURE__ */ new Float32Array(SWEEP_COUNT * 6);
function expandBoundsAt(startIndex, primitiveBounds, bounds, offset) {
  for (let d = 0; d < 3; d++) {
    let center = primitiveBounds[startIndex + 2 * d], half = primitiveBounds[startIndex + 2 * d + 1], min = center - half, max = center + half;
    min < bounds[offset + d] && (bounds[offset + d] = min), max > bounds[offset + d + 3] && (bounds[offset + d + 3] = max);
  }
}
function computeSurfaceAreaAt(array, offset) {
  let d0 = array[offset + 3] - array[offset], d1 = array[offset + 4] - array[offset + 1], d2 = array[offset + 5] - array[offset + 2];
  return 2 * (d0 * d1 + d1 * d2 + d2 * d0);
}
function getOptimalSplit(nodeBoundingData, centroidBoundingData, primitiveBounds, offset, count, strategy) {
  let axis = -1, pos = 0;
  if (strategy === 0)
    axis = getLongestEdgeIndex(centroidBoundingData), axis !== -1 && (pos = (centroidBoundingData[axis] + centroidBoundingData[axis + 3]) / 2);
  else if (strategy === 1)
    axis = getLongestEdgeIndex(nodeBoundingData), axis !== -1 && (pos = getAverage(primitiveBounds, offset, count, axis));
  else if (strategy === 2) {
    let rootSurfaceArea = computeSurfaceArea(nodeBoundingData), bestCost = 1.25 * count, boundsOffset = primitiveBounds.offset || 0, cStart = (offset - boundsOffset) * 6, cEnd = (offset + count - boundsOffset) * 6;
    for (let a = 0; a < 3; a++) {
      let axisLeft = centroidBoundingData[a], binWidth = (centroidBoundingData[a + 3] - axisLeft) / BIN_COUNT;
      if (count < SWEEP_COUNT) {
        for (let i = 0; i < count; i++) {
          let c = cStart + i * 6, center = primitiveBounds[c + 2 * a], j = i;
          for (; j > 0 && primitiveBounds[sweepOrder[j - 1] + 2 * a] > center; )
            sweepOrder[j] = sweepOrder[j - 1], j--;
          sweepOrder[j] = c;
        }
        for (let d = 0; d < 3; d++)
          leftBounds[d] = 1 / 0, leftBounds[d + 3] = -1 / 0, rightBounds[d] = 1 / 0, rightBounds[d + 3] = -1 / 0;
        for (let i = count - 1; i >= 0; i--)
          expandByPrimitiveBounds(sweepOrder[i], primitiveBounds, rightBounds), sweepRightBounds.set(rightBounds, i * 6);
        for (let i = 0; i < count - 1; i++) {
          expandByPrimitiveBounds(sweepOrder[i], primitiveBounds, leftBounds);
          let candidate = primitiveBounds[sweepOrder[i + 1] + 2 * a];
          if (primitiveBounds[sweepOrder[i] + 2 * a] === candidate)
            continue;
          let leftCount = i + 1, rightCount = count - leftCount, leftProb = computeSurfaceArea(leftBounds) / rootSurfaceArea, rightProb = computeSurfaceAreaAt(sweepRightBounds, leftCount * 6) / rootSurfaceArea, cost = 1 + 1.25 * (leftProb * leftCount + rightProb * rightCount);
          cost < bestCost && (axis = a, bestCost = cost, pos = candidate);
        }
      } else {
        for (let i = 0; i < BIN_COUNT; i++) {
          binCounts[i] = 0;
          for (let d = 0; d < 3; d++)
            binBounds[i * 6 + d] = 1 / 0, binBounds[i * 6 + d + 3] = -1 / 0;
        }
        for (let c = cStart; c < cEnd; c += 6) {
          let binIndex = ~~((primitiveBounds[c + 2 * a] - axisLeft) / binWidth);
          binIndex >= BIN_COUNT && (binIndex = BIN_COUNT - 1), binCounts[binIndex]++, expandBoundsAt(c, primitiveBounds, binBounds, binIndex * 6);
        }
        let last = (BIN_COUNT - 1) * 6;
        for (let d = 0; d < 6; d++)
          binRightBounds[last + d] = binBounds[last + d];
        for (let i = BIN_COUNT - 2; i >= 0; i--) {
          let o = i * 6, next = o + 6;
          for (let d = 0; d < 3; d++)
            binRightBounds[o + d] = Math.min(binBounds[o + d], binRightBounds[next + d]), binRightBounds[o + d + 3] = Math.max(binBounds[o + d + 3], binRightBounds[next + d + 3]);
        }
        let leftCount = 0;
        for (let i = 0; i < BIN_COUNT - 1; i++) {
          let o = i * 6, binCount = binCounts[i];
          if (binCount !== 0)
            for (let d = 0; d < 3; d++)
              leftCount === 0 ? (leftBounds[d] = binBounds[o + d], leftBounds[d + 3] = binBounds[o + d + 3]) : (leftBounds[d] = Math.min(leftBounds[d], binBounds[o + d]), leftBounds[d + 3] = Math.max(leftBounds[d + 3], binBounds[o + d + 3]));
          leftCount += binCount;
          let leftProb = 0, rightProb = 0;
          leftCount !== 0 && (leftProb = computeSurfaceArea(leftBounds) / rootSurfaceArea);
          let rightCount = count - leftCount;
          rightCount !== 0 && (rightProb = computeSurfaceAreaAt(binRightBounds, o + 6) / rootSurfaceArea);
          let cost = 1 + 1.25 * (leftProb * leftCount + rightProb * rightCount);
          cost < bestCost && (axis = a, bestCost = cost, pos = axisLeft + binWidth + i * binWidth);
        }
      }
    }
  } else
    console.warn(`BVH: Invalid build strategy value ${strategy} used.`);
  return { axis, pos };
}
function getAverage(primitiveBounds, offset, count, axis) {
  let avg = 0, boundsOffset = primitiveBounds.offset;
  for (let i = offset, end = offset + count; i < end; i++)
    avg += primitiveBounds[(i - boundsOffset) * 6 + axis * 2];
  return avg / count;
}

// npm/three-mesh-bvh/src/core/build/sortUtils.js
function partition(buffer, stride, primitiveBounds, offset, count, split) {
  let left = offset, right = offset + count - 1, pos = split.pos, axisOffset = split.axis * 2, boundsOffset = primitiveBounds.offset || 0;
  for (; ; ) {
    for (; left <= right && primitiveBounds[(left - boundsOffset) * 6 + axisOffset] < pos; )
      left++;
    for (; left <= right && primitiveBounds[(right - boundsOffset) * 6 + axisOffset] >= pos; )
      right--;
    if (left < right) {
      for (let i = 0; i < stride; i++) {
        let t0 = buffer[left * stride + i];
        buffer[left * stride + i] = buffer[right * stride + i], buffer[right * stride + i] = t0;
      }
      for (let i = 0; i < 6; i++) {
        let l = left - boundsOffset, r = right - boundsOffset, tb = primitiveBounds[l * 6 + i];
        primitiveBounds[l * 6 + i] = primitiveBounds[r * 6 + i], primitiveBounds[r * 6 + i] = tb;
      }
      left++, right--;
    } else
      return left;
  }
}

// npm/three-mesh-bvh/src/core/build/buildTree.js
var MAX_POINTER = Math.pow(2, 32), _nodeBounds = /* @__PURE__ */ new Float32Array(6), _centroidBounds = /* @__PURE__ */ new Float32Array(6);
function buildTree(bvh, primitiveBounds, offset, count, options, loadRange) {
  let {
    maxDepth,
    verbose,
    targetLeafSize,
    _strictLeafSize = 1 / 0,
    strategy,
    onProgress,
    useSharedArrayBuffer = !1
  } = options, partitionBuffer = bvh.primitiveBuffer, partitionStride = bvh.primitiveBufferStride, BufferConstructor = useSharedArrayBuffer ? SharedArrayBuffer : ArrayBuffer, buffer = new BufferConstructor(32 * Math.max(1, 2 * Math.ceil(count) - 1)), float32Array = new Float32Array(buffer), uint32Array = new Uint32Array(buffer), uint16Array = new Uint16Array(buffer), nodeCount = 0, reachedMaxDepth = !1;
  return getBounds(primitiveBounds, offset, count, _nodeBounds, _centroidBounds), splitNode(offset, count, 0), buffer.slice(0, nodeCount * 32);
  function triggerProgress(primitivesProcessed) {
    onProgress && onProgress((primitivesProcessed - loadRange.offset) / loadRange.count);
  }
  function writeLeaf(node32, offset2, count2) {
    let node16 = node32 * 2;
    uint32Array[node32 + 6] = offset2, uint16Array[node16 + 14] = count2, uint16Array[node16 + 15] = 65535, triggerProgress(offset2 + count2);
  }
  function splitNode(offset2, count2, depth) {
    let nodeIndex = nodeCount++, node32 = nodeIndex * 8;
    float32Array.set(_nodeBounds, node32), !reachedMaxDepth && depth >= maxDepth && (reachedMaxDepth = !0, verbose && console.warn(`BVH: Max depth of ${maxDepth} reached when generating BVH. Consider increasing maxDepth.`));
    let mustSplit = count2 > _strictLeafSize;
    if (count2 <= targetLeafSize && !mustSplit || depth >= maxDepth) {
      writeLeaf(node32, offset2, count2);
      return;
    }
    let split = getOptimalSplit(_nodeBounds, _centroidBounds, primitiveBounds, offset2, count2, strategy), splitOffset = split.axis === -1 ? -1 : partition(partitionBuffer, partitionStride, primitiveBounds, offset2, count2, split);
    if (split.axis === -1 || splitOffset === offset2 || splitOffset === offset2 + count2) {
      if (!mustSplit) {
        writeLeaf(node32, offset2, count2);
        return;
      }
      split.axis = Math.max(0, getLongestEdgeIndex(_nodeBounds)), splitOffset = offset2 + Math.max(1, Math.floor(count2 / 2));
    }
    uint32Array[node32 + 7] = split.axis;
    let leftCount = splitOffset - offset2;
    getBounds(primitiveBounds, offset2, leftCount, _nodeBounds, _centroidBounds), splitNode(offset2, leftCount, depth + 1);
    let relativeRightIndex = nodeCount - nodeIndex;
    if (relativeRightIndex > MAX_POINTER)
      throw new Error("MeshBVH: Cannot store relative child node offset greater than 32 bits.");
    uint32Array[node32 + 6] = relativeRightIndex;
    let rightCount = count2 - leftCount;
    getBounds(primitiveBounds, splitOffset, rightCount, _nodeBounds, _centroidBounds), splitNode(splitOffset, rightCount, depth + 1);
  }
}
function buildPackedTree(bvh, options) {
  let rootRanges = bvh.getRootRanges(options.range), firstRange = rootRanges[0], lastRange = rootRanges[rootRanges.length - 1], fullRange = {
    offset: firstRange.offset,
    count: lastRange.offset + lastRange.count - firstRange.offset
  }, primitiveBounds = new Float32Array(6 * fullRange.count);
  primitiveBounds.offset = fullRange.offset, bvh.computePrimitiveBounds(fullRange.offset, fullRange.count, primitiveBounds), bvh._roots = rootRanges.map((range) => buildTree(bvh, primitiveBounds, range.offset, range.count, options, fullRange));
}

// npm/three-mesh-bvh/src/core/cast/shapecast.js
import { Box3 } from "../three/build/three.module.js";

// npm/three-mesh-bvh/src/utils/PrimitivePool.js
var PrimitivePool = class {
  constructor(getNewPrimitive) {
    this._getNewPrimitive = getNewPrimitive, this._primitives = [];
  }
  getPrimitive() {
    let primitives = this._primitives;
    return primitives.length === 0 ? this._getNewPrimitive() : primitives.pop();
  }
  releasePrimitive(primitive) {
    this._primitives.push(primitive);
  }
};

// npm/three-mesh-bvh/src/core/utils/BufferStack.js
var _BufferStack = class {
  constructor() {
    this.float32Array = null, this.uint16Array = null, this.uint32Array = null;
    let stack = [], prevBuffer = null;
    this.setBuffer = (buffer) => {
      prevBuffer && stack.push(prevBuffer), prevBuffer = buffer, this.float32Array = new Float32Array(buffer), this.uint16Array = new Uint16Array(buffer), this.uint32Array = new Uint32Array(buffer);
    }, this.clearBuffer = () => {
      prevBuffer = null, this.float32Array = null, this.uint16Array = null, this.uint32Array = null, stack.length !== 0 && this.setBuffer(stack.pop());
    };
  }
}, BufferStack = /* @__PURE__ */ new _BufferStack();

// npm/three-mesh-bvh/src/core/cast/shapecast.js
var _box1, _box2, boxStack = [], boxPool = /* @__PURE__ */ new PrimitivePool(() => new Box3());
function shapecast(bvh, root, intersectsBounds, intersectsRange, boundsTraverseOrder, nodeOffset) {
  _box1 = boxPool.getPrimitive(), _box2 = boxPool.getPrimitive(), boxStack.push(_box1, _box2), BufferStack.setBuffer(bvh._roots[root]);
  let result = shapecastTraverse(0, bvh.geometry, intersectsBounds, intersectsRange, boundsTraverseOrder, nodeOffset);
  BufferStack.clearBuffer(), boxPool.releasePrimitive(_box1), boxPool.releasePrimitive(_box2), boxStack.pop(), boxStack.pop();
  let length = boxStack.length;
  return length > 0 && (_box2 = boxStack[length - 1], _box1 = boxStack[length - 2]), result;
}
function shapecastTraverse(nodeIndex32, geometry, intersectsBoundsFunc, intersectsRangeFunc, nodeScoreFunc = null, nodeIndexOffset = 0, depth = 0) {
  let { float32Array, uint16Array, uint32Array } = BufferStack, nodeIndex16 = nodeIndex32 * 2;
  if (IS_LEAF(nodeIndex16, uint16Array)) {
    let offset = OFFSET(nodeIndex32, uint32Array), count = COUNT(nodeIndex16, uint16Array);
    return arrayToBox(nodeIndex32, float32Array, _box1), intersectsRangeFunc(offset, count, !1, depth, nodeIndexOffset + nodeIndex32 / 8, _box1);
  } else {
    let getLeftOffset = function(nodeIndex322) {
      let { uint16Array: uint16Array2, uint32Array: uint32Array2 } = BufferStack, nodeIndex162 = nodeIndex322 * 2;
      for (; !IS_LEAF(nodeIndex162, uint16Array2); )
        nodeIndex322 = LEFT_NODE(nodeIndex322), nodeIndex162 = nodeIndex322 * 2;
      return OFFSET(nodeIndex322, uint32Array2);
    }, getRightEndOffset = function(nodeIndex322) {
      let { uint16Array: uint16Array2, uint32Array: uint32Array2 } = BufferStack, nodeIndex162 = nodeIndex322 * 2;
      for (; !IS_LEAF(nodeIndex162, uint16Array2); )
        nodeIndex322 = RIGHT_NODE(nodeIndex322, uint32Array2), nodeIndex162 = nodeIndex322 * 2;
      return OFFSET(nodeIndex322, uint32Array2) + COUNT(nodeIndex162, uint16Array2);
    }, left = LEFT_NODE(nodeIndex32), right = RIGHT_NODE(nodeIndex32, uint32Array), c1 = left, c2 = right, score1, score2, box1, box2;
    if (nodeScoreFunc && (box1 = _box1, box2 = _box2, arrayToBox(c1, float32Array, box1), arrayToBox(c2, float32Array, box2), score1 = nodeScoreFunc(box1), score2 = nodeScoreFunc(box2), score2 < score1)) {
      c1 = right, c2 = left;
      let temp5 = score1;
      score1 = score2, score2 = temp5, box1 = box2;
    }
    box1 || (box1 = _box1, arrayToBox(c1, float32Array, box1));
    let isC1Leaf = IS_LEAF(c1 * 2, uint16Array), c1Intersection = intersectsBoundsFunc(box1, isC1Leaf, score1, depth + 1, nodeIndexOffset + c1 / 8), c1StopTraversal;
    if (c1Intersection === 2) {
      let offset = getLeftOffset(c1), count = getRightEndOffset(c1) - offset;
      c1StopTraversal = intersectsRangeFunc(offset, count, !0, depth + 1, nodeIndexOffset + c1 / 8, box1);
    } else
      c1StopTraversal = c1Intersection && shapecastTraverse(
        c1,
        geometry,
        intersectsBoundsFunc,
        intersectsRangeFunc,
        nodeScoreFunc,
        nodeIndexOffset,
        depth + 1
      );
    if (c1StopTraversal) return !0;
    box2 = _box2, arrayToBox(c2, float32Array, box2);
    let isC2Leaf = IS_LEAF(c2 * 2, uint16Array), c2Intersection = intersectsBoundsFunc(box2, isC2Leaf, score2, depth + 1, nodeIndexOffset + c2 / 8), c2StopTraversal;
    if (c2Intersection === 2) {
      let offset = getLeftOffset(c2), count = getRightEndOffset(c2) - offset;
      c2StopTraversal = intersectsRangeFunc(offset, count, !0, depth + 1, nodeIndexOffset + c2 / 8, box2);
    } else
      c2StopTraversal = c2Intersection && shapecastTraverse(
        c2,
        geometry,
        intersectsBoundsFunc,
        intersectsRangeFunc,
        nodeScoreFunc,
        nodeIndexOffset,
        depth + 1
      );
    return !!c2StopTraversal;
  }
}

// npm/three-mesh-bvh/src/core/cast/bvhcast.js
import { Box3 as Box32, Matrix4 } from "../three/build/three.module.js";
var _bufferStack1 = /* @__PURE__ */ new BufferStack.constructor(), _bufferStack2 = /* @__PURE__ */ new BufferStack.constructor(), _boxPool = /* @__PURE__ */ new PrimitivePool(() => new Box32()), _leftBox1 = /* @__PURE__ */ new Box32(), _rightBox1 = /* @__PURE__ */ new Box32(), _leftBox2 = /* @__PURE__ */ new Box32(), _rightBox2 = /* @__PURE__ */ new Box32(), _active = !1;
function bvhcast(bvh, otherBvh, matrixToLocal, intersectsRanges) {
  if (_active)
    throw new Error("MeshBVH: Recursive calls to bvhcast not supported.");
  _active = !0;
  let roots = bvh._roots, otherRoots = otherBvh._roots, result, nodeOffset1 = 0, nodeOffset2 = 0, invMat = new Matrix4().copy(matrixToLocal).invert();
  for (let i = 0, il = roots.length; i < il; i++) {
    _bufferStack1.setBuffer(roots[i]), nodeOffset2 = 0;
    let localBox = _boxPool.getPrimitive();
    arrayToBox(0, _bufferStack1.float32Array, localBox), localBox.applyMatrix4(invMat);
    for (let j = 0, jl = otherRoots.length; j < jl && (_bufferStack2.setBuffer(otherRoots[j]), result = _traverse(
      0,
      0,
      matrixToLocal,
      invMat,
      intersectsRanges,
      nodeOffset1,
      nodeOffset2,
      0,
      0,
      localBox
    ), _bufferStack2.clearBuffer(), nodeOffset2 += otherRoots[j].byteLength / 32, !result); j++)
      ;
    if (_boxPool.releasePrimitive(localBox), _bufferStack1.clearBuffer(), nodeOffset1 += roots[i].byteLength / 32, result)
      break;
  }
  return _active = !1, result;
}
function _traverse(node1Index32, node2Index32, matrix2to1, matrix1to2, intersectsRangesFunc, node1IndexOffset = 0, node2IndexOffset = 0, depth1 = 0, depth2 = 0, currBox = null, reversed = !1) {
  let bufferStack1, bufferStack2;
  reversed ? (bufferStack1 = _bufferStack2, bufferStack2 = _bufferStack1) : (bufferStack1 = _bufferStack1, bufferStack2 = _bufferStack2);
  let float32Array1 = bufferStack1.float32Array, uint32Array1 = bufferStack1.uint32Array, uint16Array1 = bufferStack1.uint16Array, float32Array2 = bufferStack2.float32Array, uint32Array2 = bufferStack2.uint32Array, uint16Array2 = bufferStack2.uint16Array, node1Index16 = node1Index32 * 2, node2Index16 = node2Index32 * 2, isLeaf1 = IS_LEAF(node1Index16, uint16Array1), isLeaf2 = IS_LEAF(node2Index16, uint16Array2), result = !1;
  if (isLeaf2 && isLeaf1)
    reversed ? result = intersectsRangesFunc(
      OFFSET(node2Index32, uint32Array2),
      COUNT(node2Index32 * 2, uint16Array2),
      OFFSET(node1Index32, uint32Array1),
      COUNT(node1Index32 * 2, uint16Array1),
      depth2,
      node2IndexOffset + node2Index32 / 8,
      depth1,
      node1IndexOffset + node1Index32 / 8
    ) : result = intersectsRangesFunc(
      OFFSET(node1Index32, uint32Array1),
      COUNT(node1Index32 * 2, uint16Array1),
      OFFSET(node2Index32, uint32Array2),
      COUNT(node2Index32 * 2, uint16Array2),
      depth1,
      node1IndexOffset + node1Index32 / 8,
      depth2,
      node2IndexOffset + node2Index32 / 8
    );
  else if (isLeaf2) {
    let newBox = _boxPool.getPrimitive();
    arrayToBox(node2Index32, float32Array2, newBox), newBox.applyMatrix4(matrix2to1);
    let cl1 = LEFT_NODE(node1Index32), cr1 = RIGHT_NODE(node1Index32, uint32Array1);
    arrayToBox(cl1, float32Array1, _leftBox1), arrayToBox(cr1, float32Array1, _rightBox1);
    let intersectCl1 = newBox.intersectsBox(_leftBox1), intersectCr1 = newBox.intersectsBox(_rightBox1);
    result = intersectCl1 && _traverse(
      node2Index32,
      cl1,
      matrix1to2,
      matrix2to1,
      intersectsRangesFunc,
      node2IndexOffset,
      node1IndexOffset,
      depth2,
      depth1 + 1,
      newBox,
      !reversed
    ) || intersectCr1 && _traverse(
      node2Index32,
      cr1,
      matrix1to2,
      matrix2to1,
      intersectsRangesFunc,
      node2IndexOffset,
      node1IndexOffset,
      depth2,
      depth1 + 1,
      newBox,
      !reversed
    ), _boxPool.releasePrimitive(newBox);
  } else {
    let cl2 = LEFT_NODE(node2Index32), cr2 = RIGHT_NODE(node2Index32, uint32Array2);
    arrayToBox(cl2, float32Array2, _leftBox2), arrayToBox(cr2, float32Array2, _rightBox2);
    let leftIntersects = currBox.intersectsBox(_leftBox2), rightIntersects = currBox.intersectsBox(_rightBox2);
    if (leftIntersects && rightIntersects)
      result = _traverse(
        node1Index32,
        cl2,
        matrix2to1,
        matrix1to2,
        intersectsRangesFunc,
        node1IndexOffset,
        node2IndexOffset,
        depth1,
        depth2 + 1,
        currBox,
        reversed
      ) || _traverse(
        node1Index32,
        cr2,
        matrix2to1,
        matrix1to2,
        intersectsRangesFunc,
        node1IndexOffset,
        node2IndexOffset,
        depth1,
        depth2 + 1,
        currBox,
        reversed
      );
    else if (leftIntersects)
      if (isLeaf1)
        result = _traverse(
          node1Index32,
          cl2,
          matrix2to1,
          matrix1to2,
          intersectsRangesFunc,
          node1IndexOffset,
          node2IndexOffset,
          depth1,
          depth2 + 1,
          currBox,
          reversed
        );
      else {
        let newBox = _boxPool.getPrimitive();
        newBox.copy(_leftBox2).applyMatrix4(matrix2to1);
        let cl1 = LEFT_NODE(node1Index32), cr1 = RIGHT_NODE(node1Index32, uint32Array1);
        arrayToBox(cl1, float32Array1, _leftBox1), arrayToBox(cr1, float32Array1, _rightBox1);
        let intersectCl1 = newBox.intersectsBox(_leftBox1), intersectCr1 = newBox.intersectsBox(_rightBox1);
        result = intersectCl1 && _traverse(
          cl2,
          cl1,
          matrix1to2,
          matrix2to1,
          intersectsRangesFunc,
          node2IndexOffset,
          node1IndexOffset,
          depth2,
          depth1 + 1,
          newBox,
          !reversed
        ) || intersectCr1 && _traverse(
          cl2,
          cr1,
          matrix1to2,
          matrix2to1,
          intersectsRangesFunc,
          node2IndexOffset,
          node1IndexOffset,
          depth2,
          depth1 + 1,
          newBox,
          !reversed
        ), _boxPool.releasePrimitive(newBox);
      }
    else if (rightIntersects)
      if (isLeaf1)
        result = _traverse(
          node1Index32,
          cr2,
          matrix2to1,
          matrix1to2,
          intersectsRangesFunc,
          node1IndexOffset,
          node2IndexOffset,
          depth1,
          depth2 + 1,
          currBox,
          reversed
        );
      else {
        let newBox = _boxPool.getPrimitive();
        newBox.copy(_rightBox2).applyMatrix4(matrix2to1);
        let cl1 = LEFT_NODE(node1Index32), cr1 = RIGHT_NODE(node1Index32, uint32Array1);
        arrayToBox(cl1, float32Array1, _leftBox1), arrayToBox(cr1, float32Array1, _rightBox1);
        let intersectCl1 = newBox.intersectsBox(_leftBox1), intersectCr1 = newBox.intersectsBox(_rightBox1);
        result = intersectCl1 && _traverse(
          cr2,
          cl1,
          matrix1to2,
          matrix2to1,
          intersectsRangesFunc,
          node2IndexOffset,
          node1IndexOffset,
          depth2,
          depth1 + 1,
          newBox,
          !reversed
        ) || intersectCr1 && _traverse(
          cr2,
          cr1,
          matrix1to2,
          matrix2to1,
          intersectsRangesFunc,
          node2IndexOffset,
          node1IndexOffset,
          depth2,
          depth1 + 1,
          newBox,
          !reversed
        ), _boxPool.releasePrimitive(newBox);
      }
  }
  return result;
}

// npm/three-mesh-bvh/src/core/BVHTraversalHelper.js
var BVHTraversalHelper = new class {
  constructor() {
    let buffer = null, uint32Array = null, uint16Array = null, traversing = !1;
    this.root = null, this.buffer = null, this.uint32Array = null, this.uint16Array = null, this.setBVH = (bvh, root) => {
      if (traversing)
        throw new Error("BVHTraversalHelper: cannot call setBVH during an active traversal.");
      this.root = root, this.buffer = buffer = bvh._roots[root], this.uint16Array = uint16Array = new Uint16Array(buffer), this.uint32Array = uint32Array = new Uint32Array(buffer);
    }, this.reset = () => {
      this.root = null, this.buffer = buffer = null, this.uint16Array = uint16Array = null, this.uint32Array = uint32Array = null;
    }, this.getRangeStart = (node32Index) => {
      let node16Index = node32Index * 2;
      for (; !IS_LEAF(node16Index, uint16Array); )
        node32Index = LEFT_NODE(node32Index), node16Index = node32Index * 2;
      return OFFSET(node32Index, uint32Array);
    }, this.getRangeEnd = (node32Index) => {
      let node16Index = node32Index * 2;
      for (; !IS_LEAF(node16Index, uint16Array); )
        node32Index = RIGHT_NODE(node32Index, uint32Array), node16Index = node32Index * 2;
      return OFFSET(node32Index, uint32Array) + COUNT(node16Index, uint16Array);
    };
    let walk = (callback, node32Index, depth) => {
      let node16Index = node32Index * 2, isLeaf = IS_LEAF(node16Index, uint16Array);
      if (!callback(depth, isLeaf, node32Index) && !isLeaf) {
        let left = LEFT_NODE(node32Index), right = RIGHT_NODE(node32Index, uint32Array);
        walk(callback, left, depth + 1), walk(callback, right, depth + 1);
      }
    };
    this.traverseBuffer = (callback) => {
      if (traversing)
        throw new Error("BVHTraversalHelper: cannot start a traversal during an active traversal.");
      traversing = !0;
      try {
        walk(callback, 0, 0);
      } finally {
        traversing = !1;
      }
    }, this.traverse = (callback) => {
      this.traverseBuffer((depth, isLeaf, node32Index) => {
        if (isLeaf) {
          let node16Index = node32Index * 2, offset = uint32Array[node32Index + 6], count = uint16Array[node16Index + 14];
          return callback(depth, isLeaf, new Float32Array(buffer, node32Index * 4, 6), offset, count);
        } else {
          let splitAxis = SPLIT_AXIS(node32Index, uint32Array);
          return callback(depth, isLeaf, new Float32Array(buffer, node32Index * 4, 6), splitAxis);
        }
      });
    };
  }
}();

// npm/three-mesh-bvh/src/core/BVH.js
var _tempBox = /* @__PURE__ */ new Box33(), _tempBuffer = /* @__PURE__ */ new Float32Array(6), BVH = class {
  constructor() {
    this._roots = null, this.primitiveBuffer = null, this.primitiveBufferStride = null;
  }
  init(options) {
    options = {
      ...DEFAULT_OPTIONS,
      ...options
    }, "maxLeafSize" in options && (console.warn('BVH: "maxLeafSize" option has been deprecated. Use "targetLeafSize", instead.'), options = {
      ...options,
      targetLeafSize: options.maxLeafSize
    }), buildPackedTree(this, options);
  }
  getRootRanges() {
    throw new Error("BVH: getRootRanges() not implemented");
  }
  // write the i-th primitive bounds in a 6-value min / max format to the buffer
  // starting at the given "writeOffset"
  writePrimitiveBounds() {
    throw new Error("BVH: writePrimitiveBounds() not implemented");
  }
  // writes the union bounds of all primitives in the given range in a min / max format
  // to the buffer
  writePrimitiveRangeBounds(offset, count, targetBuffer, baseIndex) {
    let minX = 1 / 0, minY = 1 / 0, minZ = 1 / 0, maxX = -1 / 0, maxY = -1 / 0, maxZ = -1 / 0;
    for (let i = offset, end = offset + count; i < end; i++) {
      this.writePrimitiveBounds(i, _tempBuffer, 0);
      let [lx, ly, lz, rx, ry, rz] = _tempBuffer;
      lx < minX && (minX = lx), rx > maxX && (maxX = rx), ly < minY && (minY = ly), ry > maxY && (maxY = ry), lz < minZ && (minZ = lz), rz > maxZ && (maxZ = rz);
    }
    return targetBuffer[baseIndex + 0] = minX, targetBuffer[baseIndex + 1] = minY, targetBuffer[baseIndex + 2] = minZ, targetBuffer[baseIndex + 3] = maxX, targetBuffer[baseIndex + 4] = maxY, targetBuffer[baseIndex + 5] = maxZ, targetBuffer;
  }
  computePrimitiveBounds(offset, count, targetBuffer) {
    let boundsOffset = targetBuffer.offset || 0;
    for (let i = offset, end = offset + count; i < end; i++) {
      this.writePrimitiveBounds(i, _tempBuffer, 0);
      let [lx, ly, lz, rx, ry, rz] = _tempBuffer, cx = (lx + rx) / 2, cy = (ly + ry) / 2, cz = (lz + rz) / 2, hx = (rx - lx) / 2, hy = (ry - ly) / 2, hz = (rz - lz) / 2, baseIndex = (i - boundsOffset) * 6;
      targetBuffer[baseIndex + 0] = cx, targetBuffer[baseIndex + 1] = hx + (Math.abs(cx) + hx) * FLOAT32_EPSILON, targetBuffer[baseIndex + 2] = cy, targetBuffer[baseIndex + 3] = hy + (Math.abs(cy) + hy) * FLOAT32_EPSILON, targetBuffer[baseIndex + 4] = cz, targetBuffer[baseIndex + 5] = hz + (Math.abs(cz) + hz) * FLOAT32_EPSILON;
    }
    return targetBuffer;
  }
  /**
   * Adjusts all primitive offsets stored in the BVH leaf nodes by the given value. Useful when
   * geometry buffers have been shifted or compacted (e.g. when merging geometries).
   * @param {number} offset
   */
  shiftPrimitiveOffsets(offset) {
    let indirectBuffer = this._indirectBuffer;
    if (indirectBuffer)
      for (let i = 0, l = indirectBuffer.length; i < l; i++)
        indirectBuffer[i] += offset;
    else {
      let roots = this._roots;
      for (let rootIndex = 0; rootIndex < roots.length; rootIndex++) {
        let root = roots[rootIndex], uint32Array = new Uint32Array(root), uint16Array = new Uint16Array(root), totalNodes = root.byteLength / 32;
        for (let node = 0; node < totalNodes; node++) {
          let node32Index = 8 * node, node16Index = 2 * node32Index;
          IS_LEAF(node16Index, uint16Array) && (uint32Array[node32Index + 6] += offset);
        }
      }
    }
  }
  /**
   * Traverses all nodes of the BVH, invoking a callback for each node.
   *
   * For leaf nodes the callback receives `( depth, isLeaf, boundingData, offset, count )`.
   * For internal nodes it receives `( depth, isLeaf, boundingData, splitAxis )` and may
   * return `true` to stop descending into that node's children.
   *
   * @param {Function} callback
   * @param {number} [rootIndex=0]
   */
  traverse(callback, rootIndex = 0) {
    BVHTraversalHelper.setBVH(this, rootIndex), BVHTraversalHelper.traverse(callback), BVHTraversalHelper.reset();
  }
  /**
   * Refits all BVH node bounds to reflect the current primitive positions. Faster than
   * rebuilding the BVH but produces a less optimal tree after large vertex deformations.
   */
  refit() {
    let roots = this._roots;
    for (let rootIndex = 0, rootCount = roots.length; rootIndex < rootCount; rootIndex++) {
      let buffer = roots[rootIndex], uint32Array = new Uint32Array(buffer), uint16Array = new Uint16Array(buffer), float32Array = new Float32Array(buffer), totalNodes = buffer.byteLength / 32;
      for (let nodeIndex = totalNodes - 1; nodeIndex >= 0; nodeIndex--) {
        let nodeIndex32 = nodeIndex * 8, nodeIndex16 = nodeIndex32 * 2;
        if (IS_LEAF(nodeIndex16, uint16Array)) {
          let offset = OFFSET(nodeIndex32, uint32Array), count = COUNT(nodeIndex16, uint16Array);
          this.writePrimitiveRangeBounds(offset, count, _tempBuffer, 0), float32Array.set(_tempBuffer, nodeIndex32);
        } else {
          let left = LEFT_NODE(nodeIndex32), right = RIGHT_NODE(nodeIndex32, uint32Array);
          for (let i = 0; i < 3; i++) {
            let leftMin = float32Array[left + i], leftMax = float32Array[left + i + 3], rightMin = float32Array[right + i], rightMax = float32Array[right + i + 3];
            float32Array[nodeIndex32 + i] = leftMin < rightMin ? leftMin : rightMin, float32Array[nodeIndex32 + i + 3] = leftMax > rightMax ? leftMax : rightMax;
          }
        }
      }
    }
  }
  /**
   * Computes the axis-aligned bounding box of all primitives in the BVH.
   * @param {Box3} target - Target box to write the result into.
   * @returns {Box3}
   */
  getBoundingBox(target2) {
    return target2.makeEmpty(), this._roots.forEach((buffer) => {
      arrayToBox(0, new Float32Array(buffer), _tempBox), target2.union(_tempBox);
    }), target2;
  }
  /**
   * A generalized traversal function for performing spatial queries against the BVH. Returns
   * `true` as soon as a primitive has been reported as intersected. The tree is traversed
   * depth-first; `boundsTraverseOrder` controls which child is visited first. Returning
   * `CONTAINED` from `intersectsBounds` skips further child traversal and intersects all
   * primitives in that subtree immediately.
   *
   * @param {Object} callbacks
   * @param {IntersectsBoundsCallback} callbacks.intersectsBounds
   * @param {IntersectsRangeCallback} [callbacks.intersectsRange]
   * @param {BoundsTraverseOrderCallback} [callbacks.boundsTraverseOrder]
   * @returns {boolean}
   */
  // TODO: see if we can get rid of "iterateFunc" here as well as the primitive so the function
  // API aligns with the "shapecast" implementation
  shapecast(callbacks) {
    let {
      boundsTraverseOrder,
      intersectsBounds,
      intersectsRange,
      intersectsPrimitive,
      scratchPrimitive,
      iterate
    } = callbacks;
    if (intersectsRange && intersectsPrimitive) {
      let originalIntersectsRange = intersectsRange;
      intersectsRange = (offset, count, contained, depth, nodeIndex) => originalIntersectsRange(offset, count, contained, depth, nodeIndex) ? !0 : iterate(offset, count, this, intersectsPrimitive, contained, depth, scratchPrimitive);
    } else intersectsRange || (intersectsPrimitive ? intersectsRange = (offset, count, contained, depth) => iterate(offset, count, this, intersectsPrimitive, contained, depth, scratchPrimitive) : intersectsRange = (offset, count, contained) => contained);
    let result = !1, nodeOffset = 0, roots = this._roots;
    for (let i = 0, l = roots.length; i < l; i++) {
      let root = roots[i];
      if (result = shapecast(this, i, intersectsBounds, intersectsRange, boundsTraverseOrder, nodeOffset), result)
        break;
      nodeOffset += root.byteLength / 32;
    }
    return result;
  }
  /**
   * Simultaneously traverses two BVH structures to find intersecting primitive pairs. Returns
   * `true` as soon as any intersection is reported. Both trees are traversed depth-first with
   * alternating descent. `matrixToLocal` transforms `otherBvh` into the local space of this BVH.
   *
   * @param {BVH} otherBvh
   * @param {Matrix4} matrixToLocal
   * @param {Object} callbacks
   * @param {IntersectsRangesCallback} callbacks.intersectsRanges
   * @returns {boolean}
   */
  bvhcast(otherBvh, matrixToLocal, callbacks) {
    let { intersectsRanges } = callbacks;
    return bvhcast(this, otherBvh, matrixToLocal, intersectsRanges);
  }
};

// npm/three-mesh-bvh/src/core/GeometryBVH.js
import { Box3 as Box34 } from "../three/build/three.module.js";

// npm/three-mesh-bvh/src/utils/BufferUtils.js
function isSharedArrayBufferSupported() {
  return typeof SharedArrayBuffer < "u";
}

// npm/three-mesh-bvh/src/core/build/geometryUtils.js
import { BufferAttribute } from "../three/build/three.module.js";
function getVertexCount(geo) {
  return geo.index ? geo.index.count : geo.attributes.position.count;
}
function getTriCount(geo) {
  return getVertexCount(geo) / 3;
}
function getIndexArray(vertexCount, BufferConstructor = ArrayBuffer) {
  return vertexCount > 65535 ? new Uint32Array(new BufferConstructor(4 * vertexCount)) : new Uint16Array(new BufferConstructor(2 * vertexCount));
}
function ensureIndex(geo, options) {
  if (!geo.index) {
    let vertexCount = geo.attributes.position.count, BufferConstructor = options.useSharedArrayBuffer ? SharedArrayBuffer : ArrayBuffer, index = getIndexArray(vertexCount, BufferConstructor);
    geo.setIndex(new BufferAttribute(index, 1));
    for (let i = 0; i < vertexCount; i++)
      index[i] = i;
  }
}
function getFullPrimitiveRange(geo, range, stride) {
  let primitiveCount = getVertexCount(geo) / stride, drawRange = range || geo.drawRange, start = drawRange.start / stride, end = (drawRange.start + drawRange.count) / stride, offset = Math.max(0, start), count = Math.min(primitiveCount, end) - offset;
  return {
    offset: Math.floor(offset),
    count: Math.floor(count)
  };
}
function getPrimitiveGroupRanges(geo, stride) {
  return geo.groups.map((group) => ({
    offset: group.start / stride,
    count: group.count / stride
  }));
}
function getRootPrimitiveRanges(geo, range, stride) {
  let drawRange = getFullPrimitiveRange(geo, range, stride), primitiveRanges = getPrimitiveGroupRanges(geo, stride);
  if (!primitiveRanges.length)
    return [drawRange];
  let ranges = [], drawRangeStart = drawRange.offset, drawRangeEnd = drawRange.offset + drawRange.count, primitiveCount = getVertexCount(geo) / stride, events = [];
  for (let group of primitiveRanges) {
    let { offset, count } = group, groupStart = offset, groupCount = isFinite(count) ? count : primitiveCount - offset, groupEnd = offset + groupCount;
    groupStart < drawRangeEnd && groupEnd > drawRangeStart && (events.push({ pos: Math.max(drawRangeStart, groupStart), isStart: !0 }), events.push({ pos: Math.min(drawRangeEnd, groupEnd), isStart: !1 }));
  }
  events.sort((a, b) => a.pos !== b.pos ? a.pos - b.pos : a.type === "end" ? -1 : 1);
  let activeGroups = 0, lastPos = null;
  for (let event of events) {
    let newPos = event.pos;
    activeGroups !== 0 && newPos !== lastPos && ranges.push({
      offset: lastPos,
      count: newPos - lastPos
    }), activeGroups += event.isStart ? 1 : -1, lastPos = newPos;
  }
  return ranges;
}

// npm/three-mesh-bvh/src/core/GeometryBVH.js
function generateIndirectBuffer(ranges, useSharedArrayBuffer) {
  let lastRange = ranges[ranges.length - 1], useUint32 = lastRange.offset + lastRange.count > 2 ** 16, length = ranges.reduce((acc, val) => acc + val.count, 0), byteCount = useUint32 ? 4 : 2, buffer = useSharedArrayBuffer ? new SharedArrayBuffer(length * byteCount) : new ArrayBuffer(length * byteCount), indirectBuffer = useUint32 ? new Uint32Array(buffer) : new Uint16Array(buffer), index = 0;
  for (let r = 0; r < ranges.length; r++) {
    let { offset, count } = ranges[r];
    for (let i = 0; i < count; i++)
      indirectBuffer[index + i] = offset + i;
    index += count;
  }
  return indirectBuffer;
}
var GeometryBVH = class extends BVH {
  /**
   * Whether the BVH was built in indirect mode.
   * @type {boolean}
   * @readonly
   */
  get indirect() {
    return !!this._indirectBuffer;
  }
  get primitiveStride() {
    return null;
  }
  get primitiveBufferStride() {
    return this.indirect ? 1 : this.primitiveStride;
  }
  set primitiveBufferStride(v2) {
  }
  get primitiveBuffer() {
    return this.indirect ? this._indirectBuffer : this.geometry.index.array;
  }
  set primitiveBuffer(v2) {
  }
  constructor(geometry, options = {}) {
    if (geometry.isBufferGeometry) {
      if (geometry.index && geometry.index.isInterleavedBufferAttribute)
        throw new Error("BVH: InterleavedBufferAttribute is not supported for the index attribute.");
    } else throw new Error("BVH: Only BufferGeometries are supported.");
    if (options.useSharedArrayBuffer && !isSharedArrayBufferSupported())
      throw new Error("BVH: SharedArrayBuffer is not available.");
    super(), this.geometry = geometry, this.resolvePrimitiveIndex = options.indirect ? (i) => this._indirectBuffer[i] : (i) => i, this.primitiveBuffer = null, this.primitiveBufferStride = null, this._indirectBuffer = null, options = {
      ...DEFAULT_OPTIONS,
      ...options
    }, options[SKIP_GENERATION] || this.init(options);
  }
  init(options) {
    let { geometry, primitiveStride } = this;
    if (options.indirect) {
      let ranges = getRootPrimitiveRanges(geometry, options.range, primitiveStride), indirectBuffer = generateIndirectBuffer(ranges, options.useSharedArrayBuffer);
      this._indirectBuffer = indirectBuffer;
    } else
      ensureIndex(geometry, options);
    super.init(options), !geometry.boundingBox && options.setBoundingBox && (geometry.boundingBox = this.getBoundingBox(new Box34()));
  }
  // Abstract methods to be implemented by subclasses
  getRootRanges(range) {
    return this.indirect ? [{ offset: 0, count: this._indirectBuffer.length }] : getRootPrimitiveRanges(this.geometry, range, this.primitiveStride);
  }
  raycastObject3D() {
    throw new Error("BVH: raycastObject3D() not implemented");
  }
};

// npm/three-mesh-bvh/src/core/MeshBVH.js
import { BufferAttribute as BufferAttribute2, FrontSide, Ray, Vector3 as Vector310, Matrix4 as Matrix47 } from "../three/build/three.module.js";

// npm/three-mesh-bvh/src/math/OrientedBox.js
import { Vector3 as Vector34, Matrix4 as Matrix42, Line3 as Line33 } from "../three/build/three.module.js";

// npm/three-mesh-bvh/src/math/SeparatingAxisBounds.js
import { Vector3 } from "../three/build/three.module.js";
var SeparatingAxisBounds = class {
  constructor() {
    this.min = 1 / 0, this.max = -1 / 0;
  }
  setFromPointsField(points, field) {
    let min = 1 / 0, max = -1 / 0;
    for (let i = 0, l = points.length; i < l; i++) {
      let val = points[i][field];
      min = val < min ? val : min, max = val > max ? val : max;
    }
    this.min = min, this.max = max;
  }
  setFromPoints(axis, points) {
    let min = 1 / 0, max = -1 / 0;
    for (let i = 0, l = points.length; i < l; i++) {
      let p = points[i], val = axis.dot(p);
      min = val < min ? val : min, max = val > max ? val : max;
    }
    this.min = min, this.max = max;
  }
  isSeparated(other) {
    return this.min > other.max || other.min > this.max;
  }
};
SeparatingAxisBounds.prototype.setFromBox = /* @__PURE__ */ (function() {
  let p = /* @__PURE__ */ new Vector3();
  return function(axis, box) {
    let boxMin = box.min, boxMax = box.max, min = 1 / 0, max = -1 / 0;
    for (let x = 0; x <= 1; x++)
      for (let y = 0; y <= 1; y++)
        for (let z = 0; z <= 1; z++) {
          p.x = boxMin.x * x + boxMax.x * (1 - x), p.y = boxMin.y * y + boxMax.y * (1 - y), p.z = boxMin.z * z + boxMax.z * (1 - z);
          let val = axis.dot(p);
          min = Math.min(val, min), max = Math.max(val, max);
        }
    this.min = min, this.max = max;
  };
})();

// npm/three-mesh-bvh/src/math/ExtendedTriangle.js
import { Triangle, Vector3 as Vector33, Vector2 as Vector22, Line3 as Line32, Plane as Plane2 } from "../three/build/three.module.js";

// npm/three-mesh-bvh/src/math/MathUtilities.js
import { Vector3 as Vector32, Vector2, Plane, Line3 } from "../three/build/three.module.js";
var closestPointLineToLine = /* @__PURE__ */ (function() {
  let dir1 = /* @__PURE__ */ new Vector32(), dir2 = /* @__PURE__ */ new Vector32(), v02 = /* @__PURE__ */ new Vector32();
  return function(l1, l2, result) {
    let v0 = l1.start, v10 = dir1, v2 = l2.start, v32 = dir2;
    v02.subVectors(v0, v2), dir1.subVectors(l1.end, l1.start), dir2.subVectors(l2.end, l2.start);
    let d0232 = v02.dot(v32), d3210 = v32.dot(v10), d3232 = v32.dot(v32), d0210 = v02.dot(v10), denom = v10.dot(v10) * d3232 - d3210 * d3210, d, d2;
    denom !== 0 ? d = (d0232 * d3210 - d0210 * d3232) / denom : d = 0, d2 = (d0232 + d * d3210) / d3232, result.x = d, result.y = d2;
  };
})(), closestPointsSegmentToSegment = /* @__PURE__ */ (function() {
  let paramResult = /* @__PURE__ */ new Vector2(), temp14 = /* @__PURE__ */ new Vector32(), temp23 = /* @__PURE__ */ new Vector32();
  return function(l1, l2, target1, target2) {
    closestPointLineToLine(l1, l2, paramResult);
    let d = paramResult.x, d2 = paramResult.y;
    if (d >= 0 && d <= 1 && d2 >= 0 && d2 <= 1) {
      l1.at(d, target1), l2.at(d2, target2);
      return;
    } else if (d >= 0 && d <= 1) {
      d2 < 0 ? l2.at(0, target2) : l2.at(1, target2), l1.closestPointToPoint(target2, !0, target1);
      return;
    } else if (d2 >= 0 && d2 <= 1) {
      d < 0 ? l1.at(0, target1) : l1.at(1, target1), l2.closestPointToPoint(target1, !0, target2);
      return;
    } else {
      let p;
      d < 0 ? p = l1.start : p = l1.end;
      let p2;
      d2 < 0 ? p2 = l2.start : p2 = l2.end;
      let closestPoint = temp14, closestPoint2 = temp23;
      if (l1.closestPointToPoint(p2, !0, temp14), l2.closestPointToPoint(p, !0, temp23), closestPoint.distanceToSquared(p2) <= closestPoint2.distanceToSquared(p)) {
        target1.copy(closestPoint), target2.copy(p2);
        return;
      } else {
        target1.copy(p), target2.copy(closestPoint2);
        return;
      }
    }
  };
})(), sphereIntersectTriangle = /* @__PURE__ */ (function() {
  let closestPointTemp = /* @__PURE__ */ new Vector32(), projectedPointTemp = /* @__PURE__ */ new Vector32(), planeTemp = /* @__PURE__ */ new Plane(), lineTemp = /* @__PURE__ */ new Line3();
  return function(sphere, triangle4) {
    let { radius, center } = sphere, { a, b, c } = triangle4;
    if (lineTemp.start = a, lineTemp.end = b, lineTemp.closestPointToPoint(center, !0, closestPointTemp).distanceTo(center) <= radius || (lineTemp.start = a, lineTemp.end = c, lineTemp.closestPointToPoint(center, !0, closestPointTemp).distanceTo(center) <= radius) || (lineTemp.start = b, lineTemp.end = c, lineTemp.closestPointToPoint(center, !0, closestPointTemp).distanceTo(center) <= radius)) return !0;
    let plane = triangle4.getPlane(planeTemp);
    if (Math.abs(plane.distanceToPoint(center)) <= radius) {
      let pp = plane.projectPoint(center, projectedPointTemp);
      if (triangle4.containsPoint(pp)) return !0;
    }
    return !1;
  };
})();

// npm/three-mesh-bvh/src/math/ExtendedTriangle.js
var componentKeys = ["x", "y", "z"], ZERO_EPSILON = 1e-15, ZERO_EPSILON_SQR = ZERO_EPSILON * ZERO_EPSILON;
function isNearZero(value) {
  return Math.abs(value) < ZERO_EPSILON;
}
var ExtendedTriangle = class extends Triangle {
  constructor(...args) {
    super(...args), this.isExtendedTriangle = !0, this.satAxes = new Array(4).fill().map(() => new Vector33()), this.satBounds = new Array(4).fill().map(() => new SeparatingAxisBounds()), this.points = [this.a, this.b, this.c], this.plane = new Plane2(), this.isDegenerateIntoSegment = !1, this.isDegenerateIntoPoint = !1, this.degenerateSegment = new Line32(), this.needsUpdate = !0;
  }
  /**
   * Returns whether the triangle intersects the given sphere.
   * @param {Sphere} sphere
   * @returns {boolean}
   */
  intersectsSphere(sphere) {
    return sphereIntersectTriangle(sphere, this);
  }
  update() {
    let a = this.a, b = this.b, c = this.c, points = this.points, satAxes = this.satAxes, satBounds = this.satBounds, axis0 = satAxes[0], sab0 = satBounds[0];
    this.getNormal(axis0), sab0.setFromPoints(axis0, points);
    let axis1 = satAxes[1], sab1 = satBounds[1];
    axis1.subVectors(a, b), sab1.setFromPoints(axis1, points);
    let axis2 = satAxes[2], sab2 = satBounds[2];
    axis2.subVectors(b, c), sab2.setFromPoints(axis2, points);
    let axis3 = satAxes[3], sab3 = satBounds[3];
    axis3.subVectors(c, a), sab3.setFromPoints(axis3, points);
    let lengthAB = axis1.length(), lengthBC = axis2.length(), lengthCA = axis3.length();
    this.isDegenerateIntoPoint = !1, this.isDegenerateIntoSegment = !1, lengthAB < ZERO_EPSILON ? lengthBC < ZERO_EPSILON || lengthCA < ZERO_EPSILON ? this.isDegenerateIntoPoint = !0 : (this.isDegenerateIntoSegment = !0, this.degenerateSegment.start.copy(a), this.degenerateSegment.end.copy(c)) : lengthBC < ZERO_EPSILON ? lengthCA < ZERO_EPSILON ? this.isDegenerateIntoPoint = !0 : (this.isDegenerateIntoSegment = !0, this.degenerateSegment.start.copy(b), this.degenerateSegment.end.copy(a)) : lengthCA < ZERO_EPSILON && (this.isDegenerateIntoSegment = !0, this.degenerateSegment.start.copy(c), this.degenerateSegment.end.copy(b)), this.plane.setFromNormalAndCoplanarPoint(axis0, a), this.needsUpdate = !1;
  }
};
ExtendedTriangle.prototype.closestPointToSegment = /* @__PURE__ */ (function() {
  let point1 = /* @__PURE__ */ new Vector33(), point2 = /* @__PURE__ */ new Vector33(), edge = /* @__PURE__ */ new Line32();
  return function(segment, target1 = null, target2 = null) {
    this.needsUpdate && this.update();
    let { start, end } = segment, points = this.points, distSq, closestDistanceSq = 1 / 0, startDist = this.plane.distanceToPoint(start), endDist = this.plane.distanceToPoint(end);
    if (startDist * endDist < 0 && this.plane.intersectLine(segment, point1) !== null && this.containsPoint(point1))
      return target1 && target1.copy(point1), target2 && target2.copy(point1), 0;
    for (let i = 0; i < 3; i++) {
      let nexti = (i + 1) % 3;
      edge.start.copy(points[i]), edge.end.copy(points[nexti]), closestPointsSegmentToSegment(edge, segment, point1, point2), distSq = point1.distanceToSquared(point2), distSq < closestDistanceSq && (closestDistanceSq = distSq, target1 && target1.copy(point1), target2 && target2.copy(point2));
    }
    return this.closestPointToPoint(start, point1), distSq = start.distanceToSquared(point1), distSq < closestDistanceSq && (closestDistanceSq = distSq, target1 && target1.copy(point1), target2 && target2.copy(start)), this.closestPointToPoint(end, point1), distSq = end.distanceToSquared(point1), distSq < closestDistanceSq && (closestDistanceSq = distSq, target1 && target1.copy(point1), target2 && target2.copy(end)), Math.sqrt(closestDistanceSq);
  };
})();
ExtendedTriangle.prototype.intersectsTriangle = /* @__PURE__ */ (function() {
  let saTri2 = /* @__PURE__ */ new ExtendedTriangle(), cachedSatBounds = /* @__PURE__ */ new SeparatingAxisBounds(), cachedSatBounds2 = /* @__PURE__ */ new SeparatingAxisBounds(), tmpVec = /* @__PURE__ */ new Vector33(), dir1 = /* @__PURE__ */ new Vector33(), dir2 = /* @__PURE__ */ new Vector33(), tempDir = /* @__PURE__ */ new Vector33(), edge1 = /* @__PURE__ */ new Line32(), edge2 = /* @__PURE__ */ new Line32(), tempPoint = /* @__PURE__ */ new Vector33(), bounds1 = /* @__PURE__ */ new Vector22(), bounds2 = /* @__PURE__ */ new Vector22();
  function coplanarIntersectsTriangle(self, other, target2, suppressLog) {
    let planeNormal = tmpVec;
    !self.isDegenerateIntoPoint && !self.isDegenerateIntoSegment ? planeNormal.copy(self.plane.normal) : planeNormal.copy(other.plane.normal);
    let satBounds1 = self.satBounds, satAxes1 = self.satAxes;
    for (let i = 1; i < 4; i++) {
      let sb = satBounds1[i], sa = satAxes1[i];
      if (cachedSatBounds.setFromPoints(sa, other.points), sb.isSeparated(cachedSatBounds) || (tempDir.copy(planeNormal).cross(sa), cachedSatBounds.setFromPoints(tempDir, self.points), cachedSatBounds2.setFromPoints(tempDir, other.points), cachedSatBounds.isSeparated(cachedSatBounds2))) return !1;
    }
    let satBounds2 = other.satBounds, satAxes2 = other.satAxes;
    for (let i = 1; i < 4; i++) {
      let sb = satBounds2[i], sa = satAxes2[i];
      if (cachedSatBounds.setFromPoints(sa, self.points), sb.isSeparated(cachedSatBounds) || (tempDir.crossVectors(planeNormal, sa), cachedSatBounds.setFromPoints(tempDir, self.points), cachedSatBounds2.setFromPoints(tempDir, other.points), cachedSatBounds.isSeparated(cachedSatBounds2))) return !1;
    }
    return target2 && (suppressLog || console.warn("ExtendedTriangle.intersectsTriangle: Triangles are coplanar which does not support an output edge. Setting edge to 0, 0, 0."), target2.start.set(0, 0, 0), target2.end.set(0, 0, 0)), !0;
  }
  function findSingleBounds(a, b, c, aProj, bProj, cProj, aDist, bDist, cDist, bounds, edge) {
    let t = aDist / (aDist - bDist);
    bounds.x = aProj + (bProj - aProj) * t, edge.start.subVectors(b, a).multiplyScalar(t).add(a), t = aDist / (aDist - cDist), bounds.y = aProj + (cProj - aProj) * t, edge.end.subVectors(c, a).multiplyScalar(t).add(a);
  }
  function findIntersectionLineBounds(self, aProj, bProj, cProj, abDist, acDist, aDist, bDist, cDist, bounds, edge) {
    if (abDist > 0)
      findSingleBounds(self.c, self.a, self.b, cProj, aProj, bProj, cDist, aDist, bDist, bounds, edge);
    else if (acDist > 0)
      findSingleBounds(self.b, self.a, self.c, bProj, aProj, cProj, bDist, aDist, cDist, bounds, edge);
    else if (bDist * cDist > 0 || aDist != 0)
      findSingleBounds(self.a, self.b, self.c, aProj, bProj, cProj, aDist, bDist, cDist, bounds, edge);
    else if (bDist != 0)
      findSingleBounds(self.b, self.a, self.c, bProj, aProj, cProj, bDist, aDist, cDist, bounds, edge);
    else if (cDist != 0)
      findSingleBounds(self.c, self.a, self.b, cProj, aProj, bProj, cDist, aDist, bDist, bounds, edge);
    else
      return !0;
    return !1;
  }
  function intersectTriangleSegment(triangle4, degenerateTriangle, target2, suppressLog) {
    let segment = degenerateTriangle.degenerateSegment, startDist = triangle4.plane.distanceToPoint(segment.start), endDist = triangle4.plane.distanceToPoint(segment.end);
    return isNearZero(startDist) ? isNearZero(endDist) ? coplanarIntersectsTriangle(triangle4, degenerateTriangle, target2, suppressLog) : (target2 && (target2.start.copy(segment.start), target2.end.copy(segment.start)), triangle4.containsPoint(segment.start)) : isNearZero(endDist) ? (target2 && (target2.start.copy(segment.end), target2.end.copy(segment.end)), triangle4.containsPoint(segment.end)) : triangle4.plane.intersectLine(segment, tmpVec) != null ? (target2 && (target2.start.copy(tmpVec), target2.end.copy(tmpVec)), triangle4.containsPoint(tmpVec)) : !1;
  }
  function intersectTrianglePoint(triangle4, degenerateTriangle, target2) {
    let point = degenerateTriangle.a;
    return isNearZero(triangle4.plane.distanceToPoint(point)) && triangle4.containsPoint(point) ? (target2 && (target2.start.copy(point), target2.end.copy(point)), !0) : !1;
  }
  function intersectSegmentPoint(segmentTri, pointTri, target2) {
    let segment = segmentTri.degenerateSegment, point = pointTri.a;
    return segment.closestPointToPoint(point, !0, tmpVec), point.distanceToSquared(tmpVec) < ZERO_EPSILON_SQR ? (target2 && (target2.start.copy(point), target2.end.copy(point)), !0) : !1;
  }
  function handleDegenerateCases(self, other, target2, suppressLog) {
    if (self.isDegenerateIntoSegment)
      if (other.isDegenerateIntoSegment) {
        let segment1 = self.degenerateSegment, segment2 = other.degenerateSegment, delta1 = dir1, delta2 = dir2;
        segment1.delta(delta1), segment2.delta(delta2);
        let startDelta = tmpVec.subVectors(segment2.start, segment1.start), denom = delta1.x * delta2.y - delta1.y * delta2.x;
        if (isNearZero(denom))
          return !1;
        let t = (startDelta.x * delta2.y - startDelta.y * delta2.x) / denom, u2 = -(delta1.x * startDelta.y - delta1.y * startDelta.x) / denom;
        if (t < 0 || t > 1 || u2 < 0 || u2 > 1)
          return !1;
        let z1 = segment1.start.z + delta1.z * t, z2 = segment2.start.z + delta2.z * u2;
        return isNearZero(z1 - z2) ? (target2 && (target2.start.copy(segment1.start).addScaledVector(delta1, t), target2.end.copy(segment1.start).addScaledVector(delta1, t)), !0) : !1;
      } else return other.isDegenerateIntoPoint ? intersectSegmentPoint(self, other, target2) : intersectTriangleSegment(other, self, target2, suppressLog);
    else {
      if (self.isDegenerateIntoPoint)
        return other.isDegenerateIntoPoint ? other.a.distanceToSquared(self.a) < ZERO_EPSILON_SQR ? (target2 && (target2.start.copy(self.a), target2.end.copy(self.a)), !0) : !1 : other.isDegenerateIntoSegment ? intersectSegmentPoint(other, self, target2) : intersectTrianglePoint(other, self, target2);
      if (other.isDegenerateIntoPoint)
        return intersectTrianglePoint(self, other, target2);
      if (other.isDegenerateIntoSegment)
        return intersectTriangleSegment(self, other, target2, suppressLog);
    }
  }
  return function(other, target2 = null, suppressLog = !1) {
    this.needsUpdate && this.update(), other.isExtendedTriangle ? other.needsUpdate && other.update() : (saTri2.copy(other), saTri2.update(), other = saTri2);
    let res = handleDegenerateCases(this, other, target2, suppressLog);
    if (res !== void 0)
      return res;
    let plane1 = this.plane, plane2 = other.plane, a1Dist = plane2.distanceToPoint(this.a), b1Dist = plane2.distanceToPoint(this.b), c1Dist = plane2.distanceToPoint(this.c);
    isNearZero(a1Dist) && (a1Dist = 0), isNearZero(b1Dist) && (b1Dist = 0), isNearZero(c1Dist) && (c1Dist = 0);
    let a1b1Dist = a1Dist * b1Dist, a1c1Dist = a1Dist * c1Dist;
    if (a1b1Dist > 0 && a1c1Dist > 0)
      return !1;
    let a2Dist = plane1.distanceToPoint(other.a), b2Dist = plane1.distanceToPoint(other.b), c2Dist = plane1.distanceToPoint(other.c);
    isNearZero(a2Dist) && (a2Dist = 0), isNearZero(b2Dist) && (b2Dist = 0), isNearZero(c2Dist) && (c2Dist = 0);
    let a2b2Dist = a2Dist * b2Dist, a2c2Dist = a2Dist * c2Dist;
    if (a2b2Dist > 0 && a2c2Dist > 0)
      return !1;
    dir1.copy(plane1.normal), dir2.copy(plane2.normal);
    let intersectionLine = dir1.cross(dir2), componentIndex = 0, maxComponent = Math.abs(intersectionLine.x), comp1 = Math.abs(intersectionLine.y);
    comp1 > maxComponent && (maxComponent = comp1, componentIndex = 1), Math.abs(intersectionLine.z) > maxComponent && (componentIndex = 2);
    let key = componentKeys[componentIndex], a1Proj = this.a[key], b1Proj = this.b[key], c1Proj = this.c[key], a2Proj = other.a[key], b2Proj = other.b[key], c2Proj = other.c[key];
    if (findIntersectionLineBounds(this, a1Proj, b1Proj, c1Proj, a1b1Dist, a1c1Dist, a1Dist, b1Dist, c1Dist, bounds1, edge1))
      return coplanarIntersectsTriangle(this, other, target2, suppressLog);
    if (findIntersectionLineBounds(other, a2Proj, b2Proj, c2Proj, a2b2Dist, a2c2Dist, a2Dist, b2Dist, c2Dist, bounds2, edge2))
      return coplanarIntersectsTriangle(this, other, target2, suppressLog);
    if (bounds1.y < bounds1.x) {
      let tmp = bounds1.y;
      bounds1.y = bounds1.x, bounds1.x = tmp, tempPoint.copy(edge1.start), edge1.start.copy(edge1.end), edge1.end.copy(tempPoint);
    }
    if (bounds2.y < bounds2.x) {
      let tmp = bounds2.y;
      bounds2.y = bounds2.x, bounds2.x = tmp, tempPoint.copy(edge2.start), edge2.start.copy(edge2.end), edge2.end.copy(tempPoint);
    }
    return bounds1.y < bounds2.x || bounds2.y < bounds1.x ? !1 : (target2 && (bounds2.x > bounds1.x ? target2.start.copy(edge2.start) : target2.start.copy(edge1.start), bounds2.y < bounds1.y ? target2.end.copy(edge2.end) : target2.end.copy(edge1.end)), !0);
  };
})();
ExtendedTriangle.prototype.distanceToPoint = /* @__PURE__ */ (function() {
  let target2 = /* @__PURE__ */ new Vector33();
  return function(point) {
    return this.closestPointToPoint(point, target2), point.distanceTo(target2);
  };
})();
ExtendedTriangle.prototype.distanceToTriangle = /* @__PURE__ */ (function() {
  let point = /* @__PURE__ */ new Vector33(), point2 = /* @__PURE__ */ new Vector33(), cornerFields = ["a", "b", "c"], line1 = /* @__PURE__ */ new Line32(), line2 = /* @__PURE__ */ new Line32();
  return function(other, target1 = null, target2 = null) {
    let lineTarget = target1 || target2 ? line1 : null;
    if (this.intersectsTriangle(other, lineTarget, !0))
      return (target1 || target2) && (target1 && lineTarget.getCenter(target1), target2 && lineTarget.getCenter(target2)), 0;
    let closestDistanceSq = 1 / 0;
    for (let i = 0; i < 3; i++) {
      let dist, field = cornerFields[i], otherVec = other[field];
      this.closestPointToPoint(otherVec, point), dist = otherVec.distanceToSquared(point), dist < closestDistanceSq && (closestDistanceSq = dist, target1 && target1.copy(point), target2 && target2.copy(otherVec));
      let thisVec = this[field];
      other.closestPointToPoint(thisVec, point), dist = thisVec.distanceToSquared(point), dist < closestDistanceSq && (closestDistanceSq = dist, target1 && target1.copy(thisVec), target2 && target2.copy(point));
    }
    for (let i = 0; i < 3; i++) {
      let f11 = cornerFields[i], f12 = cornerFields[(i + 1) % 3];
      line1.set(this[f11], this[f12]);
      for (let i2 = 0; i2 < 3; i2++) {
        let f21 = cornerFields[i2], f22 = cornerFields[(i2 + 1) % 3];
        line2.set(other[f21], other[f22]), closestPointsSegmentToSegment(line1, line2, point, point2);
        let dist = point.distanceToSquared(point2);
        dist < closestDistanceSq && (closestDistanceSq = dist, target1 && target1.copy(point), target2 && target2.copy(point2));
      }
    }
    return Math.sqrt(closestDistanceSq);
  };
})();

// npm/three-mesh-bvh/src/math/OrientedBox.js
var OrientedBox = class {
  constructor(min, max, matrix) {
    this.isOrientedBox = !0, this.min = new Vector34(), this.max = new Vector34(), this.matrix = new Matrix42(), this.invMatrix = new Matrix42(), this.points = new Array(8).fill().map(() => new Vector34()), this.satAxes = new Array(3).fill().map(() => new Vector34()), this.satBounds = new Array(3).fill().map(() => new SeparatingAxisBounds()), this.alignedSatBounds = new Array(3).fill().map(() => new SeparatingAxisBounds()), this.needsUpdate = !1, min && this.min.copy(min), max && this.max.copy(max), matrix && this.matrix.copy(matrix);
  }
  /**
   * Sets the oriented box parameters.
   * @param {Vector3} min
   * @param {Vector3} max
   * @param {Matrix4} matrix
   */
  set(min, max, matrix) {
    this.min.copy(min), this.max.copy(max), this.matrix.copy(matrix), this.needsUpdate = !0;
  }
  copy(other) {
    this.min.copy(other.min), this.max.copy(other.max), this.matrix.copy(other.matrix), this.needsUpdate = !0;
  }
};
OrientedBox.prototype.update = /* @__PURE__ */ (function() {
  return function() {
    let matrix = this.matrix, min = this.min, max = this.max, points = this.points;
    for (let x = 0; x <= 1; x++)
      for (let y = 0; y <= 1; y++)
        for (let z = 0; z <= 1; z++) {
          let i = 1 * x | 2 * y | 4 * z, v2 = points[i];
          v2.x = x ? max.x : min.x, v2.y = y ? max.y : min.y, v2.z = z ? max.z : min.z, v2.applyMatrix4(matrix);
        }
    let satBounds = this.satBounds, satAxes = this.satAxes, minVec = points[0];
    for (let i = 0; i < 3; i++) {
      let axis = satAxes[i], sb = satBounds[i], index = 1 << i, pi = points[index];
      axis.subVectors(minVec, pi), sb.setFromPoints(axis, points);
    }
    let alignedSatBounds = this.alignedSatBounds;
    alignedSatBounds[0].setFromPointsField(points, "x"), alignedSatBounds[1].setFromPointsField(points, "y"), alignedSatBounds[2].setFromPointsField(points, "z"), this.invMatrix.copy(this.matrix).invert(), this.needsUpdate = !1;
  };
})();
OrientedBox.prototype.intersectsBox = /* @__PURE__ */ (function() {
  let aabbBounds = /* @__PURE__ */ new SeparatingAxisBounds();
  return function(box) {
    this.needsUpdate && this.update();
    let min = box.min, max = box.max, satBounds = this.satBounds, satAxes = this.satAxes, alignedSatBounds = this.alignedSatBounds;
    if (aabbBounds.min = min.x, aabbBounds.max = max.x, alignedSatBounds[0].isSeparated(aabbBounds) || (aabbBounds.min = min.y, aabbBounds.max = max.y, alignedSatBounds[1].isSeparated(aabbBounds)) || (aabbBounds.min = min.z, aabbBounds.max = max.z, alignedSatBounds[2].isSeparated(aabbBounds))) return !1;
    for (let i = 0; i < 3; i++) {
      let axis = satAxes[i], sb = satBounds[i];
      if (aabbBounds.setFromBox(axis, box), sb.isSeparated(aabbBounds)) return !1;
    }
    return !0;
  };
})();
OrientedBox.prototype.intersectsTriangle = /* @__PURE__ */ (function() {
  let saTri = /* @__PURE__ */ new ExtendedTriangle(), pointsArr = /* @__PURE__ */ new Array(3), cachedSatBounds = /* @__PURE__ */ new SeparatingAxisBounds(), cachedSatBounds2 = /* @__PURE__ */ new SeparatingAxisBounds(), cachedAxis = /* @__PURE__ */ new Vector34();
  return function(triangle4) {
    this.needsUpdate && this.update(), triangle4.isExtendedTriangle ? triangle4.needsUpdate && triangle4.update() : (saTri.copy(triangle4), saTri.update(), triangle4 = saTri);
    let satBounds = this.satBounds, satAxes = this.satAxes;
    pointsArr[0] = triangle4.a, pointsArr[1] = triangle4.b, pointsArr[2] = triangle4.c;
    for (let i = 0; i < 3; i++) {
      let sb = satBounds[i], sa = satAxes[i];
      if (cachedSatBounds.setFromPoints(sa, pointsArr), sb.isSeparated(cachedSatBounds)) return !1;
    }
    let triSatBounds = triangle4.satBounds, triSatAxes = triangle4.satAxes, points = this.points;
    for (let i = 0; i < 3; i++) {
      let sb = triSatBounds[i], sa = triSatAxes[i];
      if (cachedSatBounds.setFromPoints(sa, points), sb.isSeparated(cachedSatBounds)) return !1;
    }
    for (let i = 0; i < 3; i++) {
      let sa1 = satAxes[i];
      for (let i2 = 0; i2 < 4; i2++) {
        let sa2 = triSatAxes[i2];
        if (cachedAxis.crossVectors(sa1, sa2), cachedSatBounds.setFromPoints(cachedAxis, pointsArr), cachedSatBounds2.setFromPoints(cachedAxis, points), cachedSatBounds.isSeparated(cachedSatBounds2)) return !1;
      }
    }
    return !0;
  };
})();
OrientedBox.prototype.closestPointToPoint = /* @__PURE__ */ (function() {
  return function(point, target1) {
    return this.needsUpdate && this.update(), target1.copy(point).applyMatrix4(this.invMatrix).clamp(this.min, this.max).applyMatrix4(this.matrix), target1;
  };
})();
OrientedBox.prototype.distanceToPoint = (function() {
  let target2 = new Vector34();
  return function(point) {
    return this.closestPointToPoint(point, target2), point.distanceTo(target2);
  };
})();
OrientedBox.prototype.distanceToBox = /* @__PURE__ */ (function() {
  let xyzFields = ["x", "y", "z"], segments1 = /* @__PURE__ */ new Array(12).fill().map(() => new Line33()), segments2 = /* @__PURE__ */ new Array(12).fill().map(() => new Line33()), point1 = /* @__PURE__ */ new Vector34(), point2 = /* @__PURE__ */ new Vector34();
  return function(box, threshold = 0, target1 = null, target2 = null) {
    if (this.needsUpdate && this.update(), this.intersectsBox(box))
      return (target1 || target2) && (box.getCenter(point2), this.closestPointToPoint(point2, point1), box.closestPointToPoint(point1, point2), target1 && target1.copy(point1), target2 && target2.copy(point2)), 0;
    let threshold2 = threshold * threshold, min = box.min, max = box.max, points = this.points, closestDistanceSq = 1 / 0;
    for (let i = 0; i < 8; i++) {
      let p = points[i];
      point2.copy(p).clamp(min, max);
      let dist = p.distanceToSquared(point2);
      if (dist < closestDistanceSq && (closestDistanceSq = dist, target1 && target1.copy(p), target2 && target2.copy(point2), dist < threshold2))
        return Math.sqrt(dist);
    }
    let count = 0;
    for (let i = 0; i < 3; i++)
      for (let i1 = 0; i1 <= 1; i1++)
        for (let i2 = 0; i2 <= 1; i2++) {
          let nextIndex = (i + 1) % 3, nextIndex2 = (i + 2) % 3, index = i1 << nextIndex | i2 << nextIndex2, index2 = 1 << i | i1 << nextIndex | i2 << nextIndex2, p1 = points[index], p2 = points[index2];
          segments1[count].set(p1, p2);
          let f1 = xyzFields[i], f2 = xyzFields[nextIndex], f3 = xyzFields[nextIndex2], line2 = segments2[count], start = line2.start, end = line2.end;
          start[f1] = min[f1], start[f2] = i1 ? min[f2] : max[f2], start[f3] = i2 ? min[f3] : max[f3], end[f1] = max[f1], end[f2] = i1 ? min[f2] : max[f2], end[f3] = i2 ? min[f3] : max[f3], count++;
        }
    for (let x = 0; x <= 1; x++)
      for (let y = 0; y <= 1; y++)
        for (let z = 0; z <= 1; z++) {
          point2.x = x ? max.x : min.x, point2.y = y ? max.y : min.y, point2.z = z ? max.z : min.z, this.closestPointToPoint(point2, point1);
          let dist = point2.distanceToSquared(point1);
          if (dist < closestDistanceSq && (closestDistanceSq = dist, target1 && target1.copy(point1), target2 && target2.copy(point2), dist < threshold2))
            return Math.sqrt(dist);
        }
    for (let i = 0; i < 12; i++) {
      let l1 = segments1[i];
      for (let i2 = 0; i2 < 12; i2++) {
        let l2 = segments2[i2];
        closestPointsSegmentToSegment(l1, l2, point1, point2);
        let dist = point1.distanceToSquared(point2);
        if (dist < closestDistanceSq && (closestDistanceSq = dist, target1 && target1.copy(point1), target2 && target2.copy(point2), dist < threshold2))
          return Math.sqrt(dist);
      }
    }
    return Math.sqrt(closestDistanceSq);
  };
})();

// npm/three-mesh-bvh/src/utils/ExtendedTrianglePool.js
var ExtendedTrianglePoolBase = class extends PrimitivePool {
  constructor() {
    super(() => new ExtendedTriangle());
  }
}, ExtendedTrianglePool = /* @__PURE__ */ new ExtendedTrianglePoolBase();

// npm/three-mesh-bvh/src/core/cast/closestPointToPoint.js
import { Vector3 as Vector35 } from "../three/build/three.module.js";
var temp = /* @__PURE__ */ new Vector35(), temp1 = /* @__PURE__ */ new Vector35();
function closestPointToPoint(bvh, point, target2 = {}, minThreshold = 0, maxThreshold = 1 / 0) {
  let minThresholdSq = minThreshold * minThreshold, maxThresholdSq = maxThreshold * maxThreshold, closestDistanceSq = 1 / 0, closestDistanceTriIndex = null;
  if (bvh.shapecast(
    {
      boundsTraverseOrder: (box) => (temp.copy(point).clamp(box.min, box.max), temp.distanceToSquared(point)),
      intersectsBounds: (box, isLeaf, score) => score < closestDistanceSq && score < maxThresholdSq,
      intersectsTriangle: (tri, triIndex) => {
        tri.closestPointToPoint(point, temp);
        let distSq = point.distanceToSquared(temp);
        return distSq < closestDistanceSq && (temp1.copy(temp), closestDistanceSq = distSq, closestDistanceTriIndex = triIndex), distSq < minThresholdSq;
      }
    }
  ), closestDistanceSq === 1 / 0) return null;
  let closestDistance = Math.sqrt(closestDistanceSq);
  return target2.point ? target2.point.copy(temp1) : target2.point = temp1.clone(), target2.distance = closestDistance, target2.faceIndex = closestDistanceTriIndex, target2;
}

// npm/three-mesh-bvh/src/utils/ThreeRayIntersectUtilities.js
import { Vector3 as Vector36, Vector2 as Vector23, Triangle as Triangle2, DoubleSide, BackSide, REVISION } from "../three/build/three.module.js";
var IS_GT_REVISION_169 = parseInt(REVISION) >= 169, IS_LT_REVISION_161 = parseInt(REVISION) <= 161, _vA = /* @__PURE__ */ new Vector36(), _vB = /* @__PURE__ */ new Vector36(), _vC = /* @__PURE__ */ new Vector36(), _uvA = /* @__PURE__ */ new Vector23(), _uvB = /* @__PURE__ */ new Vector23(), _uvC = /* @__PURE__ */ new Vector23(), _normalA = /* @__PURE__ */ new Vector36(), _normalB = /* @__PURE__ */ new Vector36(), _normalC = /* @__PURE__ */ new Vector36(), _intersectionPoint = /* @__PURE__ */ new Vector36();
function checkIntersection(ray, pA, pB, pC, point, side, near, far) {
  let intersect;
  if (side === BackSide ? intersect = ray.intersectTriangle(pC, pB, pA, !0, point) : intersect = ray.intersectTriangle(pA, pB, pC, side !== DoubleSide, point), intersect === null) return null;
  let distance = ray.origin.distanceTo(point);
  return distance < near || distance > far ? null : {
    distance,
    point: point.clone()
  };
}
function checkBufferGeometryIntersection(ray, position, normal, uv, uv1, a, b, c, side, near, far) {
  _vA.fromBufferAttribute(position, a), _vB.fromBufferAttribute(position, b), _vC.fromBufferAttribute(position, c);
  let intersection = checkIntersection(ray, _vA, _vB, _vC, _intersectionPoint, side, near, far);
  if (intersection) {
    if (uv) {
      _uvA.fromBufferAttribute(uv, a), _uvB.fromBufferAttribute(uv, b), _uvC.fromBufferAttribute(uv, c), intersection.uv = new Vector23();
      let res = Triangle2.getInterpolation(_intersectionPoint, _vA, _vB, _vC, _uvA, _uvB, _uvC, intersection.uv);
      IS_GT_REVISION_169 || (intersection.uv = res);
    }
    if (uv1) {
      _uvA.fromBufferAttribute(uv1, a), _uvB.fromBufferAttribute(uv1, b), _uvC.fromBufferAttribute(uv1, c), intersection.uv1 = new Vector23();
      let res = Triangle2.getInterpolation(_intersectionPoint, _vA, _vB, _vC, _uvA, _uvB, _uvC, intersection.uv1);
      IS_GT_REVISION_169 || (intersection.uv1 = res), IS_LT_REVISION_161 && (intersection.uv2 = intersection.uv1);
    }
    if (normal) {
      _normalA.fromBufferAttribute(normal, a), _normalB.fromBufferAttribute(normal, b), _normalC.fromBufferAttribute(normal, c), intersection.normal = new Vector36();
      let res = Triangle2.getInterpolation(_intersectionPoint, _vA, _vB, _vC, _normalA, _normalB, _normalC, intersection.normal);
      intersection.normal.dot(ray.direction) > 0 && intersection.normal.multiplyScalar(-1), IS_GT_REVISION_169 || (intersection.normal = res);
    }
    let face = {
      a,
      b,
      c,
      normal: new Vector36(),
      materialIndex: 0
    };
    if (Triangle2.getNormal(_vA, _vB, _vC, face.normal), intersection.face = face, intersection.faceIndex = a, IS_GT_REVISION_169) {
      let barycoord = new Vector36();
      Triangle2.getBarycoord(_intersectionPoint, _vA, _vB, _vC, barycoord), intersection.barycoord = barycoord;
    }
  }
  return intersection;
}
function getSide(materialOrSide) {
  return materialOrSide && materialOrSide.isMaterial ? materialOrSide.side : materialOrSide;
}
function intersectTri(geometry, materialOrSide, ray, tri, intersections, near, far) {
  let triOffset = tri * 3, a = triOffset + 0, b = triOffset + 1, c = triOffset + 2, { index, groups } = geometry;
  geometry.index && (a = index.getX(a), b = index.getX(b), c = index.getX(c));
  let { position, normal, uv, uv1 } = geometry.attributes;
  if (Array.isArray(materialOrSide)) {
    let firstIndex = tri * 3;
    for (let i = 0, l = groups.length; i < l; i++) {
      let { start, count, materialIndex } = groups[i];
      if (firstIndex >= start && firstIndex < start + count) {
        let side = getSide(materialOrSide[materialIndex]), intersection = checkBufferGeometryIntersection(ray, position, normal, uv, uv1, a, b, c, side, near, far);
        if (intersection)
          if (intersection.faceIndex = tri, intersection.face.materialIndex = materialIndex, intersections)
            intersections.push(intersection);
          else
            return intersection;
      }
    }
  } else {
    let side = getSide(materialOrSide), intersection = checkBufferGeometryIntersection(ray, position, normal, uv, uv1, a, b, c, side, near, far);
    if (intersection)
      if (intersection.faceIndex = tri, intersection.face.materialIndex = 0, intersections)
        intersections.push(intersection);
      else
        return intersection;
  }
  return null;
}

// npm/three-mesh-bvh/src/utils/TriangleUtilities.js
import { Vector2 as Vector24, Vector3 as Vector37, Triangle as Triangle3 } from "../three/build/three.module.js";
function setTriangle(tri, i, index, pos) {
  let ta = tri.a, tb = tri.b, tc = tri.c, i0 = i, i1 = i + 1, i2 = i + 2;
  index && (i0 = index.getX(i0), i1 = index.getX(i1), i2 = index.getX(i2)), ta.x = pos.getX(i0), ta.y = pos.getY(i0), ta.z = pos.getZ(i0), tb.x = pos.getX(i1), tb.y = pos.getY(i1), tb.z = pos.getZ(i1), tc.x = pos.getX(i2), tc.y = pos.getY(i2), tc.z = pos.getZ(i2);
}

// npm/three-mesh-bvh/src/core/utils/iterationUtils.generated.js
function intersectTris(bvh, materialOrSide, ray, offset, count, intersections, near, far) {
  let { geometry, _indirectBuffer } = bvh;
  for (let i = offset, end = offset + count; i < end; i++)
    intersectTri(geometry, materialOrSide, ray, i, intersections, near, far);
}
function intersectClosestTri(bvh, materialOrSide, ray, offset, count, near, far) {
  let { geometry, _indirectBuffer } = bvh, dist = 1 / 0, res = null;
  for (let i = offset, end = offset + count; i < end; i++) {
    let intersection;
    intersection = intersectTri(geometry, materialOrSide, ray, i, null, near, far), intersection && intersection.distance < dist && (res = intersection, dist = intersection.distance);
  }
  return res;
}
function iterateOverTriangles(offset, count, bvh, intersectsTriangleFunc, contained, depth, triangle4) {
  let { geometry } = bvh, { index } = geometry, pos = geometry.attributes.position;
  for (let i = offset, l = count + offset; i < l; i++) {
    let tri;
    if (tri = i, setTriangle(triangle4, tri * 3, index, pos), triangle4.needsUpdate = !0, intersectsTriangleFunc(triangle4, tri, contained, depth))
      return !0;
  }
  return !1;
}

// npm/three-mesh-bvh/src/core/cast/refit.generated.js
function refit(bvh, nodeIndices = null) {
  nodeIndices && Array.isArray(nodeIndices) && (nodeIndices = new Set(nodeIndices));
  let geometry = bvh.geometry, indexArr = geometry.index ? geometry.index.array : null, posAttr = geometry.attributes.position, buffer, uint32Array, uint16Array, float32Array, byteOffset = 0, roots = bvh._roots;
  for (let i = 0, l = roots.length; i < l; i++)
    buffer = roots[i], uint32Array = new Uint32Array(buffer), uint16Array = new Uint16Array(buffer), float32Array = new Float32Array(buffer), _traverse2(0, byteOffset), byteOffset += buffer.byteLength;
  function _traverse2(nodeIndex32, byteOffset2, force = !1) {
    let nodeIndex16 = nodeIndex32 * 2;
    if (IS_LEAF(nodeIndex16, uint16Array)) {
      let offset = OFFSET(nodeIndex32, uint32Array), count = COUNT(nodeIndex16, uint16Array), minx = 1 / 0, miny = 1 / 0, minz = 1 / 0, maxx = -1 / 0, maxy = -1 / 0, maxz = -1 / 0;
      for (let i = 3 * offset, l = 3 * (offset + count); i < l; i++) {
        let index = indexArr[i], x = posAttr.getX(index), y = posAttr.getY(index), z = posAttr.getZ(index);
        x < minx && (minx = x), x > maxx && (maxx = x), y < miny && (miny = y), y > maxy && (maxy = y), z < minz && (minz = z), z > maxz && (maxz = z);
      }
      return float32Array[nodeIndex32 + 0] !== minx || float32Array[nodeIndex32 + 1] !== miny || float32Array[nodeIndex32 + 2] !== minz || float32Array[nodeIndex32 + 3] !== maxx || float32Array[nodeIndex32 + 4] !== maxy || float32Array[nodeIndex32 + 5] !== maxz ? (float32Array[nodeIndex32 + 0] = minx, float32Array[nodeIndex32 + 1] = miny, float32Array[nodeIndex32 + 2] = minz, float32Array[nodeIndex32 + 3] = maxx, float32Array[nodeIndex32 + 4] = maxy, float32Array[nodeIndex32 + 5] = maxz, !0) : !1;
    } else {
      let left = LEFT_NODE(nodeIndex32), right = RIGHT_NODE(nodeIndex32, uint32Array), forceChildren = force, includesLeft = !1, includesRight = !1;
      if (nodeIndices) {
        if (!forceChildren) {
          let leftNodeId = left / 8 + byteOffset2 / 32, rightNodeId = right / 8 + byteOffset2 / 32;
          includesLeft = nodeIndices.has(leftNodeId), includesRight = nodeIndices.has(rightNodeId), forceChildren = !includesLeft && !includesRight;
        }
      } else
        includesLeft = !0, includesRight = !0;
      let traverseLeft = forceChildren || includesLeft, traverseRight = forceChildren || includesRight, leftChange = !1;
      traverseLeft && (leftChange = _traverse2(left, byteOffset2, forceChildren));
      let rightChange = !1;
      traverseRight && (rightChange = _traverse2(right, byteOffset2, forceChildren));
      let didChange = leftChange || rightChange;
      if (didChange)
        for (let i = 0; i < 3; i++) {
          let left_i = left + i, right_i = right + i, minLeftValue = float32Array[left_i], maxLeftValue = float32Array[left_i + 3], minRightValue = float32Array[right_i], maxRightValue = float32Array[right_i + 3];
          float32Array[nodeIndex32 + i] = minLeftValue < minRightValue ? minLeftValue : minRightValue, float32Array[nodeIndex32 + i + 3] = maxLeftValue > maxRightValue ? maxLeftValue : maxRightValue;
        }
      return didChange;
    }
  }
}

// npm/three-mesh-bvh/src/core/utils/intersectUtils.js
function intersectsNodeBounds(nodeIndex32, array, ray, near, far) {
  let tmin, tmax, tymin, tymax, tzmin, tzmax, invdirx = 1 / ray.direction.x, invdiry = 1 / ray.direction.y, invdirz = 1 / ray.direction.z, ox = ray.origin.x, oy = ray.origin.y, oz = ray.origin.z, minx = array[nodeIndex32], maxx = array[nodeIndex32 + 3], miny = array[nodeIndex32 + 1], maxy = array[nodeIndex32 + 3 + 1], minz = array[nodeIndex32 + 2], maxz = array[nodeIndex32 + 3 + 2];
  return invdirx >= 0 ? (tmin = (minx - ox) * invdirx, tmax = (maxx - ox) * invdirx) : (tmin = (maxx - ox) * invdirx, tmax = (minx - ox) * invdirx), invdiry >= 0 ? (tymin = (miny - oy) * invdiry, tymax = (maxy - oy) * invdiry) : (tymin = (maxy - oy) * invdiry, tymax = (miny - oy) * invdiry), tmin > tymax || tymin > tmax || ((tymin > tmin || isNaN(tmin)) && (tmin = tymin), (tymax < tmax || isNaN(tmax)) && (tmax = tymax), invdirz >= 0 ? (tzmin = (minz - oz) * invdirz, tzmax = (maxz - oz) * invdirz) : (tzmin = (maxz - oz) * invdirz, tzmax = (minz - oz) * invdirz), tmin > tzmax || tzmin > tmax) ? !1 : ((tzmin > tmin || tmin !== tmin) && (tmin = tzmin), (tzmax < tmax || tmax !== tmax) && (tmax = tzmax), tmin <= far && tmax >= near);
}

// npm/three-mesh-bvh/src/core/utils/iterationUtils_indirect.generated.js
function intersectTris_indirect(bvh, materialOrSide, ray, offset, count, intersections, near, far) {
  let { geometry, _indirectBuffer } = bvh;
  for (let i = offset, end = offset + count; i < end; i++) {
    let vi = _indirectBuffer ? _indirectBuffer[i] : i;
    intersectTri(geometry, materialOrSide, ray, vi, intersections, near, far);
  }
}
function intersectClosestTri_indirect(bvh, materialOrSide, ray, offset, count, near, far) {
  let { geometry, _indirectBuffer } = bvh, dist = 1 / 0, res = null;
  for (let i = offset, end = offset + count; i < end; i++) {
    let intersection;
    intersection = intersectTri(geometry, materialOrSide, ray, _indirectBuffer ? _indirectBuffer[i] : i, null, near, far), intersection && intersection.distance < dist && (res = intersection, dist = intersection.distance);
  }
  return res;
}
function iterateOverTriangles_indirect(offset, count, bvh, intersectsTriangleFunc, contained, depth, triangle4) {
  let { geometry } = bvh, { index } = geometry, pos = geometry.attributes.position;
  for (let i = offset, l = count + offset; i < l; i++) {
    let tri;
    if (tri = bvh.resolveTriangleIndex(i), setTriangle(triangle4, tri * 3, index, pos), triangle4.needsUpdate = !0, intersectsTriangleFunc(triangle4, tri, contained, depth))
      return !0;
  }
  return !1;
}

// npm/three-mesh-bvh/src/core/cast/raycast.generated.js
function raycast(bvh, root, materialOrSide, ray, intersects, near, far) {
  BufferStack.setBuffer(bvh._roots[root]), intersectsNodeBounds(0, BufferStack.float32Array, ray, near, far) && _raycast(0, bvh, materialOrSide, ray, intersects, near, far), BufferStack.clearBuffer();
}
function _raycast(nodeIndex32, bvh, materialOrSide, ray, intersects, near, far) {
  let { float32Array, uint16Array, uint32Array } = BufferStack, nodeIndex16 = nodeIndex32 * 2;
  if (IS_LEAF(nodeIndex16, uint16Array)) {
    let offset = OFFSET(nodeIndex32, uint32Array), count = COUNT(nodeIndex16, uint16Array);
    intersectTris(bvh, materialOrSide, ray, offset, count, intersects, near, far);
  } else {
    let leftIndex = LEFT_NODE(nodeIndex32);
    intersectsNodeBounds(leftIndex, float32Array, ray, near, far) && _raycast(leftIndex, bvh, materialOrSide, ray, intersects, near, far);
    let rightIndex = RIGHT_NODE(nodeIndex32, uint32Array);
    intersectsNodeBounds(rightIndex, float32Array, ray, near, far) && _raycast(rightIndex, bvh, materialOrSide, ray, intersects, near, far);
  }
}

// npm/three-mesh-bvh/src/core/cast/raycastFirst.generated.js
var _xyzFields = ["x", "y", "z"];
function raycastFirst(bvh, root, materialOrSide, ray, near, far) {
  BufferStack.setBuffer(bvh._roots[root]);
  let result = null;
  return intersectsNodeBounds(0, BufferStack.float32Array, ray, near, far) && (result = _raycastFirst(0, bvh, materialOrSide, ray, near, far)), BufferStack.clearBuffer(), result;
}
function _raycastFirst(nodeIndex32, bvh, materialOrSide, ray, near, far) {
  let { float32Array, uint16Array, uint32Array } = BufferStack, nodeIndex16 = nodeIndex32 * 2;
  if (IS_LEAF(nodeIndex16, uint16Array)) {
    let offset = OFFSET(nodeIndex32, uint32Array), count = COUNT(nodeIndex16, uint16Array);
    return intersectClosestTri(bvh, materialOrSide, ray, offset, count, near, far);
  } else {
    let splitAxis = SPLIT_AXIS(nodeIndex32, uint32Array), xyzAxis = _xyzFields[splitAxis], leftToRight = ray.direction[xyzAxis] >= 0, c1, c2;
    leftToRight ? (c1 = LEFT_NODE(nodeIndex32), c2 = RIGHT_NODE(nodeIndex32, uint32Array)) : (c1 = RIGHT_NODE(nodeIndex32, uint32Array), c2 = LEFT_NODE(nodeIndex32));
    let c1Result = intersectsNodeBounds(c1, float32Array, ray, near, far) ? _raycastFirst(c1, bvh, materialOrSide, ray, near, far) : null;
    if (c1Result) {
      let point = c1Result.point[xyzAxis];
      if (leftToRight ? point <= float32Array[c2 + splitAxis] : (
        // min bounding data
        point >= float32Array[c2 + splitAxis + 3]
      ))
        return c1Result;
    }
    let c2Result = intersectsNodeBounds(c2, float32Array, ray, near, far) ? _raycastFirst(c2, bvh, materialOrSide, ray, near, far) : null;
    return c1Result && c2Result ? c1Result.distance <= c2Result.distance ? c1Result : c2Result : c1Result || c2Result || null;
  }
}

// npm/three-mesh-bvh/src/core/cast/intersectsGeometry.generated.js
import { Box3 as Box35, Matrix4 as Matrix43 } from "../three/build/three.module.js";
var boundingBox = /* @__PURE__ */ new Box35(), triangle = /* @__PURE__ */ new ExtendedTriangle(), triangle2 = /* @__PURE__ */ new ExtendedTriangle(), invertedMat = /* @__PURE__ */ new Matrix43(), obb = /* @__PURE__ */ new OrientedBox(), obb2 = /* @__PURE__ */ new OrientedBox();
function intersectsGeometry(bvh, root, otherGeometry, geometryToBvh) {
  BufferStack.setBuffer(bvh._roots[root]);
  let result = _intersectsGeometry(0, bvh, otherGeometry, geometryToBvh);
  return BufferStack.clearBuffer(), result;
}
function _intersectsGeometry(nodeIndex32, bvh, otherGeometry, geometryToBvh, cachedObb = null) {
  let { float32Array, uint16Array, uint32Array } = BufferStack, nodeIndex16 = nodeIndex32 * 2;
  if (cachedObb === null && (otherGeometry.boundingBox || otherGeometry.computeBoundingBox(), obb.set(otherGeometry.boundingBox.min, otherGeometry.boundingBox.max, geometryToBvh), cachedObb = obb), IS_LEAF(nodeIndex16, uint16Array)) {
    let thisGeometry = bvh.geometry, thisIndex = thisGeometry.index, thisPos = thisGeometry.attributes.position, otherIndex = otherGeometry.index, otherPos = otherGeometry.attributes.position, offset = OFFSET(nodeIndex32, uint32Array), count = COUNT(nodeIndex16, uint16Array);
    if (invertedMat.copy(geometryToBvh).invert(), otherGeometry.boundsTree)
      return arrayToBox(nodeIndex32, float32Array, obb2), obb2.matrix.copy(invertedMat), obb2.needsUpdate = !0, otherGeometry.boundsTree.shapecast({
        intersectsBounds: (box) => obb2.intersectsBox(box),
        intersectsTriangle: (tri) => {
          tri.a.applyMatrix4(geometryToBvh), tri.b.applyMatrix4(geometryToBvh), tri.c.applyMatrix4(geometryToBvh), tri.needsUpdate = !0;
          for (let i = offset * 3, l = (count + offset) * 3; i < l; i += 3)
            if (setTriangle(triangle2, i, thisIndex, thisPos), triangle2.needsUpdate = !0, tri.intersectsTriangle(triangle2))
              return !0;
          return !1;
        }
      });
    {
      let otherTriangleCount = getTriCount(otherGeometry);
      for (let i = offset * 3, l = (count + offset) * 3; i < l; i += 3) {
        setTriangle(triangle, i, thisIndex, thisPos), triangle.a.applyMatrix4(invertedMat), triangle.b.applyMatrix4(invertedMat), triangle.c.applyMatrix4(invertedMat), triangle.needsUpdate = !0;
        for (let i2 = 0, l2 = otherTriangleCount * 3; i2 < l2; i2 += 3)
          if (setTriangle(triangle2, i2, otherIndex, otherPos), triangle2.needsUpdate = !0, triangle.intersectsTriangle(triangle2))
            return !0;
      }
    }
  } else {
    let left = LEFT_NODE(nodeIndex32), right = RIGHT_NODE(nodeIndex32, uint32Array);
    return arrayToBox(left, float32Array, boundingBox), !!(cachedObb.intersectsBox(boundingBox) && _intersectsGeometry(left, bvh, otherGeometry, geometryToBvh, cachedObb) || (arrayToBox(right, float32Array, boundingBox), cachedObb.intersectsBox(boundingBox) && _intersectsGeometry(right, bvh, otherGeometry, geometryToBvh, cachedObb)));
  }
}

// npm/three-mesh-bvh/src/core/cast/closestPointToGeometry.generated.js
import { Matrix4 as Matrix44, Vector3 as Vector38 } from "../three/build/three.module.js";
var tempMatrix = /* @__PURE__ */ new Matrix44(), obb3 = /* @__PURE__ */ new OrientedBox(), obb22 = /* @__PURE__ */ new OrientedBox(), temp12 = /* @__PURE__ */ new Vector38(), temp2 = /* @__PURE__ */ new Vector38(), temp3 = /* @__PURE__ */ new Vector38(), temp4 = /* @__PURE__ */ new Vector38();
function closestPointToGeometry(bvh, otherGeometry, geometryToBvh, target1 = {}, target2 = {}, minThreshold = 0, maxThreshold = 1 / 0) {
  otherGeometry.boundingBox || otherGeometry.computeBoundingBox(), obb3.set(otherGeometry.boundingBox.min, otherGeometry.boundingBox.max, geometryToBvh), obb3.needsUpdate = !0;
  let geometry = bvh.geometry, pos = geometry.attributes.position, index = geometry.index, otherPos = otherGeometry.attributes.position, otherIndex = otherGeometry.index, triangle4 = ExtendedTrianglePool.getPrimitive(), triangle23 = ExtendedTrianglePool.getPrimitive(), tempTarget1 = temp12, tempTargetDest1 = temp2, tempTarget2 = null, tempTargetDest2 = null;
  target2 && (tempTarget2 = temp3, tempTargetDest2 = temp4);
  let closestDistance = 1 / 0, closestDistanceTriIndex = null, closestDistanceOtherTriIndex = null;
  return tempMatrix.copy(geometryToBvh).invert(), obb22.matrix.copy(tempMatrix), bvh.shapecast(
    {
      boundsTraverseOrder: (box) => obb3.distanceToBox(box),
      intersectsBounds: (box, isLeaf, score) => score < closestDistance && score < maxThreshold ? (isLeaf && (obb22.min.copy(box.min), obb22.max.copy(box.max), obb22.needsUpdate = !0), !0) : !1,
      intersectsRange: (offset, count) => {
        if (otherGeometry.boundsTree)
          return otherGeometry.boundsTree.shapecast({
            boundsTraverseOrder: (box) => obb22.distanceToBox(box),
            intersectsBounds: (box, isLeaf, score) => score < closestDistance && score < maxThreshold,
            intersectsRange: (otherOffset, otherCount) => {
              for (let i2 = otherOffset, l2 = otherOffset + otherCount; i2 < l2; i2++) {
                setTriangle(triangle23, 3 * i2, otherIndex, otherPos), triangle23.a.applyMatrix4(geometryToBvh), triangle23.b.applyMatrix4(geometryToBvh), triangle23.c.applyMatrix4(geometryToBvh), triangle23.needsUpdate = !0;
                for (let i = offset, l = offset + count; i < l; i++) {
                  setTriangle(triangle4, 3 * i, index, pos), triangle4.needsUpdate = !0;
                  let dist = triangle4.distanceToTriangle(triangle23, tempTarget1, tempTarget2);
                  if (dist < closestDistance && (tempTargetDest1.copy(tempTarget1), tempTargetDest2 && tempTargetDest2.copy(tempTarget2), closestDistance = dist, closestDistanceTriIndex = i, closestDistanceOtherTriIndex = i2), dist < minThreshold)
                    return !0;
                }
              }
            }
          });
        {
          let triCount = getTriCount(otherGeometry);
          for (let i2 = 0, l2 = triCount; i2 < l2; i2++) {
            setTriangle(triangle23, 3 * i2, otherIndex, otherPos), triangle23.a.applyMatrix4(geometryToBvh), triangle23.b.applyMatrix4(geometryToBvh), triangle23.c.applyMatrix4(geometryToBvh), triangle23.needsUpdate = !0;
            for (let i = offset, l = offset + count; i < l; i++) {
              setTriangle(triangle4, 3 * i, index, pos), triangle4.needsUpdate = !0;
              let dist = triangle4.distanceToTriangle(triangle23, tempTarget1, tempTarget2);
              if (dist < closestDistance && (tempTargetDest1.copy(tempTarget1), tempTargetDest2 && tempTargetDest2.copy(tempTarget2), closestDistance = dist, closestDistanceTriIndex = i, closestDistanceOtherTriIndex = i2), dist < minThreshold)
                return !0;
            }
          }
        }
      }
    }
  ), ExtendedTrianglePool.releasePrimitive(triangle4), ExtendedTrianglePool.releasePrimitive(triangle23), closestDistance === 1 / 0 ? null : (target1.point ? target1.point.copy(tempTargetDest1) : target1.point = tempTargetDest1.clone(), target1.distance = closestDistance, target1.faceIndex = closestDistanceTriIndex, target2 && (target2.point ? target2.point.copy(tempTargetDest2) : target2.point = tempTargetDest2.clone(), target2.point.applyMatrix4(tempMatrix), tempTargetDest1.applyMatrix4(tempMatrix), target2.distance = tempTargetDest1.sub(target2.point).length(), target2.faceIndex = closestDistanceOtherTriIndex), target1);
}

// npm/three-mesh-bvh/src/core/cast/refit_indirect.generated.js
function refit_indirect(bvh, nodeIndices = null) {
  nodeIndices && Array.isArray(nodeIndices) && (nodeIndices = new Set(nodeIndices));
  let geometry = bvh.geometry, indexArr = geometry.index ? geometry.index.array : null, posAttr = geometry.attributes.position, buffer, uint32Array, uint16Array, float32Array, byteOffset = 0, roots = bvh._roots;
  for (let i = 0, l = roots.length; i < l; i++)
    buffer = roots[i], uint32Array = new Uint32Array(buffer), uint16Array = new Uint16Array(buffer), float32Array = new Float32Array(buffer), _traverse2(0, byteOffset), byteOffset += buffer.byteLength;
  function _traverse2(nodeIndex32, byteOffset2, force = !1) {
    let nodeIndex16 = nodeIndex32 * 2;
    if (IS_LEAF(nodeIndex16, uint16Array)) {
      let offset = OFFSET(nodeIndex32, uint32Array), count = COUNT(nodeIndex16, uint16Array), minx = 1 / 0, miny = 1 / 0, minz = 1 / 0, maxx = -1 / 0, maxy = -1 / 0, maxz = -1 / 0;
      for (let i = offset, l = offset + count; i < l; i++) {
        let t = 3 * bvh.resolveTriangleIndex(i);
        for (let j = 0; j < 3; j++) {
          let index = t + j;
          index = indexArr ? indexArr[index] : index;
          let x = posAttr.getX(index), y = posAttr.getY(index), z = posAttr.getZ(index);
          x < minx && (minx = x), x > maxx && (maxx = x), y < miny && (miny = y), y > maxy && (maxy = y), z < minz && (minz = z), z > maxz && (maxz = z);
        }
      }
      return float32Array[nodeIndex32 + 0] !== minx || float32Array[nodeIndex32 + 1] !== miny || float32Array[nodeIndex32 + 2] !== minz || float32Array[nodeIndex32 + 3] !== maxx || float32Array[nodeIndex32 + 4] !== maxy || float32Array[nodeIndex32 + 5] !== maxz ? (float32Array[nodeIndex32 + 0] = minx, float32Array[nodeIndex32 + 1] = miny, float32Array[nodeIndex32 + 2] = minz, float32Array[nodeIndex32 + 3] = maxx, float32Array[nodeIndex32 + 4] = maxy, float32Array[nodeIndex32 + 5] = maxz, !0) : !1;
    } else {
      let left = LEFT_NODE(nodeIndex32), right = RIGHT_NODE(nodeIndex32, uint32Array), forceChildren = force, includesLeft = !1, includesRight = !1;
      if (nodeIndices) {
        if (!forceChildren) {
          let leftNodeId = left / 8 + byteOffset2 / 32, rightNodeId = right / 8 + byteOffset2 / 32;
          includesLeft = nodeIndices.has(leftNodeId), includesRight = nodeIndices.has(rightNodeId), forceChildren = !includesLeft && !includesRight;
        }
      } else
        includesLeft = !0, includesRight = !0;
      let traverseLeft = forceChildren || includesLeft, traverseRight = forceChildren || includesRight, leftChange = !1;
      traverseLeft && (leftChange = _traverse2(left, byteOffset2, forceChildren));
      let rightChange = !1;
      traverseRight && (rightChange = _traverse2(right, byteOffset2, forceChildren));
      let didChange = leftChange || rightChange;
      if (didChange)
        for (let i = 0; i < 3; i++) {
          let left_i = left + i, right_i = right + i, minLeftValue = float32Array[left_i], maxLeftValue = float32Array[left_i + 3], minRightValue = float32Array[right_i], maxRightValue = float32Array[right_i + 3];
          float32Array[nodeIndex32 + i] = minLeftValue < minRightValue ? minLeftValue : minRightValue, float32Array[nodeIndex32 + i + 3] = maxLeftValue > maxRightValue ? maxLeftValue : maxRightValue;
        }
      return didChange;
    }
  }
}

// npm/three-mesh-bvh/src/core/cast/raycast_indirect.generated.js
function raycast_indirect(bvh, root, materialOrSide, ray, intersects, near, far) {
  BufferStack.setBuffer(bvh._roots[root]), intersectsNodeBounds(0, BufferStack.float32Array, ray, near, far) && _raycast2(0, bvh, materialOrSide, ray, intersects, near, far), BufferStack.clearBuffer();
}
function _raycast2(nodeIndex32, bvh, materialOrSide, ray, intersects, near, far) {
  let { float32Array, uint16Array, uint32Array } = BufferStack, nodeIndex16 = nodeIndex32 * 2;
  if (IS_LEAF(nodeIndex16, uint16Array)) {
    let offset = OFFSET(nodeIndex32, uint32Array), count = COUNT(nodeIndex16, uint16Array);
    intersectTris_indirect(bvh, materialOrSide, ray, offset, count, intersects, near, far);
  } else {
    let leftIndex = LEFT_NODE(nodeIndex32);
    intersectsNodeBounds(leftIndex, float32Array, ray, near, far) && _raycast2(leftIndex, bvh, materialOrSide, ray, intersects, near, far);
    let rightIndex = RIGHT_NODE(nodeIndex32, uint32Array);
    intersectsNodeBounds(rightIndex, float32Array, ray, near, far) && _raycast2(rightIndex, bvh, materialOrSide, ray, intersects, near, far);
  }
}

// npm/three-mesh-bvh/src/core/cast/raycastFirst_indirect.generated.js
var _xyzFields2 = ["x", "y", "z"];
function raycastFirst_indirect(bvh, root, materialOrSide, ray, near, far) {
  BufferStack.setBuffer(bvh._roots[root]);
  let result = null;
  return intersectsNodeBounds(0, BufferStack.float32Array, ray, near, far) && (result = _raycastFirst2(0, bvh, materialOrSide, ray, near, far)), BufferStack.clearBuffer(), result;
}
function _raycastFirst2(nodeIndex32, bvh, materialOrSide, ray, near, far) {
  let { float32Array, uint16Array, uint32Array } = BufferStack, nodeIndex16 = nodeIndex32 * 2;
  if (IS_LEAF(nodeIndex16, uint16Array)) {
    let offset = OFFSET(nodeIndex32, uint32Array), count = COUNT(nodeIndex16, uint16Array);
    return intersectClosestTri_indirect(bvh, materialOrSide, ray, offset, count, near, far);
  } else {
    let splitAxis = SPLIT_AXIS(nodeIndex32, uint32Array), xyzAxis = _xyzFields2[splitAxis], leftToRight = ray.direction[xyzAxis] >= 0, c1, c2;
    leftToRight ? (c1 = LEFT_NODE(nodeIndex32), c2 = RIGHT_NODE(nodeIndex32, uint32Array)) : (c1 = RIGHT_NODE(nodeIndex32, uint32Array), c2 = LEFT_NODE(nodeIndex32));
    let c1Result = intersectsNodeBounds(c1, float32Array, ray, near, far) ? _raycastFirst2(c1, bvh, materialOrSide, ray, near, far) : null;
    if (c1Result) {
      let point = c1Result.point[xyzAxis];
      if (leftToRight ? point <= float32Array[c2 + splitAxis] : (
        // min bounding data
        point >= float32Array[c2 + splitAxis + 3]
      ))
        return c1Result;
    }
    let c2Result = intersectsNodeBounds(c2, float32Array, ray, near, far) ? _raycastFirst2(c2, bvh, materialOrSide, ray, near, far) : null;
    return c1Result && c2Result ? c1Result.distance <= c2Result.distance ? c1Result : c2Result : c1Result || c2Result || null;
  }
}

// npm/three-mesh-bvh/src/core/cast/intersectsGeometry_indirect.generated.js
import { Box3 as Box36, Matrix4 as Matrix45 } from "../three/build/three.module.js";
var boundingBox2 = /* @__PURE__ */ new Box36(), triangle3 = /* @__PURE__ */ new ExtendedTriangle(), triangle22 = /* @__PURE__ */ new ExtendedTriangle(), invertedMat2 = /* @__PURE__ */ new Matrix45(), obb4 = /* @__PURE__ */ new OrientedBox(), obb23 = /* @__PURE__ */ new OrientedBox();
function intersectsGeometry_indirect(bvh, root, otherGeometry, geometryToBvh) {
  BufferStack.setBuffer(bvh._roots[root]);
  let result = _intersectsGeometry2(0, bvh, otherGeometry, geometryToBvh);
  return BufferStack.clearBuffer(), result;
}
function _intersectsGeometry2(nodeIndex32, bvh, otherGeometry, geometryToBvh, cachedObb = null) {
  let { float32Array, uint16Array, uint32Array } = BufferStack, nodeIndex16 = nodeIndex32 * 2;
  if (cachedObb === null && (otherGeometry.boundingBox || otherGeometry.computeBoundingBox(), obb4.set(otherGeometry.boundingBox.min, otherGeometry.boundingBox.max, geometryToBvh), cachedObb = obb4), IS_LEAF(nodeIndex16, uint16Array)) {
    let thisGeometry = bvh.geometry, thisIndex = thisGeometry.index, thisPos = thisGeometry.attributes.position, otherIndex = otherGeometry.index, otherPos = otherGeometry.attributes.position, offset = OFFSET(nodeIndex32, uint32Array), count = COUNT(nodeIndex16, uint16Array);
    if (invertedMat2.copy(geometryToBvh).invert(), otherGeometry.boundsTree)
      return arrayToBox(nodeIndex32, float32Array, obb23), obb23.matrix.copy(invertedMat2), obb23.needsUpdate = !0, otherGeometry.boundsTree.shapecast({
        intersectsBounds: (box) => obb23.intersectsBox(box),
        intersectsTriangle: (tri) => {
          tri.a.applyMatrix4(geometryToBvh), tri.b.applyMatrix4(geometryToBvh), tri.c.applyMatrix4(geometryToBvh), tri.needsUpdate = !0;
          for (let i = offset, l = count + offset; i < l; i++)
            if (setTriangle(triangle22, 3 * bvh.resolveTriangleIndex(i), thisIndex, thisPos), triangle22.needsUpdate = !0, tri.intersectsTriangle(triangle22))
              return !0;
          return !1;
        }
      });
    {
      let otherTriangleCount = getTriCount(otherGeometry);
      for (let i = offset, l = count + offset; i < l; i++) {
        let ti = bvh.resolveTriangleIndex(i);
        setTriangle(triangle3, 3 * ti, thisIndex, thisPos), triangle3.a.applyMatrix4(invertedMat2), triangle3.b.applyMatrix4(invertedMat2), triangle3.c.applyMatrix4(invertedMat2), triangle3.needsUpdate = !0;
        for (let i2 = 0, l2 = otherTriangleCount * 3; i2 < l2; i2 += 3)
          if (setTriangle(triangle22, i2, otherIndex, otherPos), triangle22.needsUpdate = !0, triangle3.intersectsTriangle(triangle22))
            return !0;
      }
    }
  } else {
    let left = LEFT_NODE(nodeIndex32), right = RIGHT_NODE(nodeIndex32, uint32Array);
    return arrayToBox(left, float32Array, boundingBox2), !!(cachedObb.intersectsBox(boundingBox2) && _intersectsGeometry2(left, bvh, otherGeometry, geometryToBvh, cachedObb) || (arrayToBox(right, float32Array, boundingBox2), cachedObb.intersectsBox(boundingBox2) && _intersectsGeometry2(right, bvh, otherGeometry, geometryToBvh, cachedObb)));
  }
}

// npm/three-mesh-bvh/src/core/cast/closestPointToGeometry_indirect.generated.js
import { Matrix4 as Matrix46, Vector3 as Vector39 } from "../three/build/three.module.js";
var tempMatrix2 = /* @__PURE__ */ new Matrix46(), obb5 = /* @__PURE__ */ new OrientedBox(), obb24 = /* @__PURE__ */ new OrientedBox(), temp13 = /* @__PURE__ */ new Vector39(), temp22 = /* @__PURE__ */ new Vector39(), temp32 = /* @__PURE__ */ new Vector39(), temp42 = /* @__PURE__ */ new Vector39();
function closestPointToGeometry_indirect(bvh, otherGeometry, geometryToBvh, target1 = {}, target2 = {}, minThreshold = 0, maxThreshold = 1 / 0) {
  otherGeometry.boundingBox || otherGeometry.computeBoundingBox(), obb5.set(otherGeometry.boundingBox.min, otherGeometry.boundingBox.max, geometryToBvh), obb5.needsUpdate = !0;
  let geometry = bvh.geometry, pos = geometry.attributes.position, index = geometry.index, otherPos = otherGeometry.attributes.position, otherIndex = otherGeometry.index, triangle4 = ExtendedTrianglePool.getPrimitive(), triangle23 = ExtendedTrianglePool.getPrimitive(), tempTarget1 = temp13, tempTargetDest1 = temp22, tempTarget2 = null, tempTargetDest2 = null;
  target2 && (tempTarget2 = temp32, tempTargetDest2 = temp42);
  let closestDistance = 1 / 0, closestDistanceTriIndex = null, closestDistanceOtherTriIndex = null;
  return tempMatrix2.copy(geometryToBvh).invert(), obb24.matrix.copy(tempMatrix2), bvh.shapecast(
    {
      boundsTraverseOrder: (box) => obb5.distanceToBox(box),
      intersectsBounds: (box, isLeaf, score) => score < closestDistance && score < maxThreshold ? (isLeaf && (obb24.min.copy(box.min), obb24.max.copy(box.max), obb24.needsUpdate = !0), !0) : !1,
      intersectsRange: (offset, count) => {
        if (otherGeometry.boundsTree) {
          let otherBvh = otherGeometry.boundsTree;
          return otherBvh.shapecast({
            boundsTraverseOrder: (box) => obb24.distanceToBox(box),
            intersectsBounds: (box, isLeaf, score) => score < closestDistance && score < maxThreshold,
            intersectsRange: (otherOffset, otherCount) => {
              for (let i2 = otherOffset, l2 = otherOffset + otherCount; i2 < l2; i2++) {
                let ti2 = otherBvh.resolveTriangleIndex(i2);
                setTriangle(triangle23, 3 * ti2, otherIndex, otherPos), triangle23.a.applyMatrix4(geometryToBvh), triangle23.b.applyMatrix4(geometryToBvh), triangle23.c.applyMatrix4(geometryToBvh), triangle23.needsUpdate = !0;
                for (let i = offset, l = offset + count; i < l; i++) {
                  let ti = bvh.resolveTriangleIndex(i);
                  setTriangle(triangle4, 3 * ti, index, pos), triangle4.needsUpdate = !0;
                  let dist = triangle4.distanceToTriangle(triangle23, tempTarget1, tempTarget2);
                  if (dist < closestDistance && (tempTargetDest1.copy(tempTarget1), tempTargetDest2 && tempTargetDest2.copy(tempTarget2), closestDistance = dist, closestDistanceTriIndex = i, closestDistanceOtherTriIndex = i2), dist < minThreshold)
                    return !0;
                }
              }
            }
          });
        } else {
          let triCount = getTriCount(otherGeometry);
          for (let i2 = 0, l2 = triCount; i2 < l2; i2++) {
            setTriangle(triangle23, 3 * i2, otherIndex, otherPos), triangle23.a.applyMatrix4(geometryToBvh), triangle23.b.applyMatrix4(geometryToBvh), triangle23.c.applyMatrix4(geometryToBvh), triangle23.needsUpdate = !0;
            for (let i = offset, l = offset + count; i < l; i++) {
              let ti = bvh.resolveTriangleIndex(i);
              setTriangle(triangle4, 3 * ti, index, pos), triangle4.needsUpdate = !0;
              let dist = triangle4.distanceToTriangle(triangle23, tempTarget1, tempTarget2);
              if (dist < closestDistance && (tempTargetDest1.copy(tempTarget1), tempTargetDest2 && tempTargetDest2.copy(tempTarget2), closestDistance = dist, closestDistanceTriIndex = i, closestDistanceOtherTriIndex = i2), dist < minThreshold)
                return !0;
            }
          }
        }
      }
    }
  ), ExtendedTrianglePool.releasePrimitive(triangle4), ExtendedTrianglePool.releasePrimitive(triangle23), closestDistance === 1 / 0 ? null : (target1.point ? target1.point.copy(tempTargetDest1) : target1.point = tempTargetDest1.clone(), target1.distance = closestDistance, target1.faceIndex = closestDistanceTriIndex, target2 && (target2.point ? target2.point.copy(tempTargetDest2) : target2.point = tempTargetDest2.clone(), target2.point.applyMatrix4(tempMatrix2), tempTargetDest1.applyMatrix4(tempMatrix2), target2.distance = tempTargetDest1.sub(target2.point).length(), target2.faceIndex = closestDistanceOtherTriIndex), target1);
}

// npm/three-mesh-bvh/src/utils/GeometryRayIntersectUtilities.js
function convertRaycastIntersect(hit, object, raycaster) {
  return hit === null ? null : (hit.point.applyMatrix4(object.matrixWorld), hit.distance = hit.point.distanceTo(raycaster.ray.origin), hit.object = object, hit);
}

// npm/three-mesh-bvh/src/core/MeshBVH.js
var _obb = /* @__PURE__ */ new OrientedBox(), _ray = /* @__PURE__ */ new Ray(), _direction = /* @__PURE__ */ new Vector310(), _inverseMatrix = /* @__PURE__ */ new Matrix47(), _worldScale = /* @__PURE__ */ new Vector310(), _getters = ["getX", "getY", "getZ"], MeshBVH = class _MeshBVH extends GeometryBVH {
  /**
   * Generates a representation of the complete bounds tree and the geometry index buffer which
   * can be used to recreate a bounds tree using the `deserialize` function. The `serialize` and
   * `deserialize` functions can be used to generate a MeshBVH asynchronously in a background web
   * worker to prevent the main thread from stuttering. The BVH roots buffer stored in the
   * serialized representation are the same as the ones used by the original BVH so they should
   * not be modified. If `SharedArrayBuffers` are used then the same BVH memory can be used for
   * multiple BVH in multiple WebWorkers.
   *
   * @static
   * @param {MeshBVH} bvh - The BVH to serialize.
   * @param {Object} [options]
   * @param {boolean} [options.cloneBuffers=true] - If `true`, the index and BVH root buffers
   *   are cloned so the serialized data is independent of the live BVH.
   * @returns {SerializedBVH}
   */
  static serialize(bvh, options = {}) {
    options = {
      cloneBuffers: !0,
      ...options
    };
    let geometry = bvh.geometry, rootData = bvh._roots, indirectBuffer = bvh._indirectBuffer, indexAttribute = geometry.getIndex(), result = {
      version: 1,
      roots: null,
      index: null,
      indirectBuffer: null
    };
    return options.cloneBuffers ? (result.roots = rootData.map((root) => root.slice()), result.index = indexAttribute ? indexAttribute.array.slice() : null, result.indirectBuffer = indirectBuffer ? indirectBuffer.slice() : null) : (result.roots = rootData, result.index = indexAttribute ? indexAttribute.array : null, result.indirectBuffer = indirectBuffer), result;
  }
  /**
   * Returns a new MeshBVH instance from the serialized data. `geometry` is the geometry used
   * to generate the original BVH `data` was derived from. The root buffers stored in `data`
   * are set directly on the new BVH so the memory is shared.
   *
   * @static
   * @param {SerializedBVH} data - Serialized BVH data.
   * @param {BufferGeometry} geometry - The geometry the BVH was originally built from.
   * @param {Object} [options]
   * @param {boolean} [options.setIndex=true] - If `true`, sets `geometry.index` from the
   *   serialized index buffer (creating one if none exists).
   * @returns {MeshBVH}
   */
  static deserialize(data, geometry, options = {}) {
    options = {
      setIndex: !0,
      indirect: !!data.indirectBuffer,
      ...options
    };
    let { index, roots, indirectBuffer } = data;
    data.version || (console.warn(
      "MeshBVH.deserialize: Serialization format has been changed and will be fixed up. It is recommended to regenerate any stored serialized data."
    ), fixupVersion0(roots));
    let bvh = new _MeshBVH(geometry, { ...options, [SKIP_GENERATION]: !0 });
    if (bvh._roots = roots, bvh._indirectBuffer = indirectBuffer || null, options.setIndex) {
      let indexAttribute = geometry.getIndex();
      if (indexAttribute === null) {
        let newIndex = new BufferAttribute2(data.index, 1, !1);
        geometry.setIndex(newIndex);
      } else indexAttribute.array !== index && (indexAttribute.array.set(index), indexAttribute.needsUpdate = !0);
    }
    return bvh;
    function fixupVersion0(roots2) {
      for (let rootIndex = 0; rootIndex < roots2.length; rootIndex++) {
        let root = roots2[rootIndex], uint32Array = new Uint32Array(root), uint16Array = new Uint16Array(root);
        for (let node = 0, l = root.byteLength / 32; node < l; node++) {
          let node32Index = 8 * node, node16Index = 2 * node32Index;
          IS_LEAF(node16Index, uint16Array) || (uint32Array[node32Index + 6] = uint32Array[node32Index + 6] / 8 - node);
        }
      }
    }
  }
  get primitiveStride() {
    return 3;
  }
  /**
   * Helper function for use when `indirect` is set to true. This function takes a triangle
   * index in the BVH layout and returns the associated triangle index in the geometry index
   * buffer or position attribute.
   * @type {function(number): number}
   * @readonly
   */
  get resolveTriangleIndex() {
    return this.resolvePrimitiveIndex;
  }
  constructor(geometry, options = {}) {
    options.maxLeafTris && (console.warn('MeshBVH: "maxLeafTris" option has been deprecated. Use "targetLeafSize", instead.'), options = {
      ...options,
      targetLeafSize: options.maxLeafTris
    }), super(geometry, options);
  }
  /**
   * Adjusts all triangle offsets stored in the BVH by the given offset. This is useful when the
   * triangle data has been compacted or shifted in the geometry buffers (e.g. in `BatchedMesh`
   * when geometries are compacted using the 'optimize' function or constructing a 'merged' BVH).
   * This function only adjusts the BVH to point to different triangles in the geometry. The
   * geometry's index buffer and/or position attributes must be updated separately to match.
   *
   * @param {number} offset
   * @returns {void}
   */
  // implement abstract methods from BVH base class
  shiftTriangleOffsets(offset) {
    return super.shiftPrimitiveOffsets(offset);
  }
  // write primitive bounds to the buffer - used only for validateBounds at the moment
  writePrimitiveBounds(i, targetBuffer, baseIndex) {
    let geometry = this.geometry, indirectBuffer = this._indirectBuffer, posAttr = geometry.attributes.position, index = geometry.index ? geometry.index.array : null, tri3 = (indirectBuffer ? indirectBuffer[i] : i) * 3, ai = tri3 + 0, bi = tri3 + 1, ci = tri3 + 2;
    index && (ai = index[ai], bi = index[bi], ci = index[ci]);
    for (let el = 0; el < 3; el++) {
      let a = posAttr[_getters[el]](ai), b = posAttr[_getters[el]](bi), c = posAttr[_getters[el]](ci), min = a;
      b < min && (min = b), c < min && (min = c);
      let max = a;
      b > max && (max = b), c > max && (max = c), targetBuffer[baseIndex + el] = min, targetBuffer[baseIndex + el + 3] = max;
    }
    return targetBuffer;
  }
  // precomputes the bounding box for each triangle; required for quickly calculating tree splits.
  // result is an array of size count * 6 where triangle i maps to a
  // [x_center, x_delta, y_center, y_delta, z_center, z_delta] tuple starting at index (i - offset) * 6,
  // representing the center and half-extent in each dimension of triangle i
  computePrimitiveBounds(offset, count, targetBuffer) {
    let geometry = this.geometry, indirectBuffer = this._indirectBuffer, posAttr = geometry.attributes.position, index = geometry.index ? geometry.index.array : null, normalized = posAttr.normalized;
    if (offset < 0 || count + offset - targetBuffer.offset > targetBuffer.length / 6)
      throw new Error("MeshBVH: compute triangle bounds range is invalid.");
    let posArr = posAttr.array, bufferOffset = posAttr.offset || 0, stride = 3;
    posAttr.isInterleavedBufferAttribute && (stride = posAttr.data.stride);
    let getters = ["getX", "getY", "getZ"], writeOffset = targetBuffer.offset;
    for (let i = offset, l = offset + count; i < l; i++) {
      let tri3 = (indirectBuffer ? indirectBuffer[i] : i) * 3, boundsIndexOffset = (i - writeOffset) * 6, ai = tri3 + 0, bi = tri3 + 1, ci = tri3 + 2;
      index && (ai = index[ai], bi = index[bi], ci = index[ci]), normalized || (ai = ai * stride + bufferOffset, bi = bi * stride + bufferOffset, ci = ci * stride + bufferOffset);
      for (let el = 0; el < 3; el++) {
        let a, b, c;
        normalized ? (a = posAttr[getters[el]](ai), b = posAttr[getters[el]](bi), c = posAttr[getters[el]](ci)) : (a = posArr[ai + el], b = posArr[bi + el], c = posArr[ci + el]);
        let min = a;
        b < min && (min = b), c < min && (min = c);
        let max = a;
        b > max && (max = b), c > max && (max = c);
        let halfExtents = (max - min) / 2, el2 = el * 2;
        targetBuffer[boundsIndexOffset + el2 + 0] = min + halfExtents, targetBuffer[boundsIndexOffset + el2 + 1] = halfExtents + (Math.abs(min) + halfExtents) * FLOAT32_EPSILON;
      }
    }
    return targetBuffer;
  }
  /**
   * A convenience function for performing a raycast based on a mesh. Results are formed like
   * three.js raycast results in world frame.
   *
   * @param {Object3D} object
   * @param {Raycaster} raycaster
   * @param {Array<Intersection>} [intersects=[]]
   * @returns {Array<Intersection>}
   */
  raycastObject3D(object, raycaster, intersects = []) {
    let { material } = object;
    if (material === void 0)
      return;
    _inverseMatrix.copy(object.matrixWorld).invert(), _ray.copy(raycaster.ray).applyMatrix4(_inverseMatrix), _worldScale.setFromMatrixScale(object.matrixWorld), _direction.copy(_ray.direction).multiply(_worldScale);
    let scaleFactor = _direction.length(), near = raycaster.near / scaleFactor, far = raycaster.far / scaleFactor;
    if (raycaster.firstHitOnly === !0) {
      let hit = this.raycastFirst(_ray, material, near, far);
      hit = convertRaycastIntersect(hit, object, raycaster), hit && intersects.push(hit);
    } else {
      let hits = this.raycast(_ray, material, near, far);
      for (let i = 0, l = hits.length; i < l; i++) {
        let hit = convertRaycastIntersect(hits[i], object, raycaster);
        hit && intersects.push(hit);
      }
    }
    return intersects;
  }
  /**
   * Refit the node bounds to the current triangle positions. This is quicker than regenerating
   * a new BVH but will not be optimal after significant changes to the vertices. `nodeIndices`
   * is a set of node indices (provided by the `shapecast` function) that need to be refit
   * including all internal nodes.
   *
   * @param {Set<number>|Array<number>|null} [nodeIndices=null]
   */
  refit(nodeIndices = null) {
    return (this.indirect ? refit_indirect : refit)(this, nodeIndices);
  }
  /* Core Cast Functions */
  /**
   * Returns all raycast triangle hits in unsorted order. It is expected that `ray` is in the
   * frame of the BVH already. Likewise the returned results are also provided in the local
   * frame of the BVH. The `side` identifier is used to determine the side to check when
   * raycasting or a material with the given side field can be passed. If an array of materials
   * is provided then it is expected that the geometry has groups and the appropriate material
   * side is used per group.
   *
   * Note that unlike three.js' Raycaster results the points and distances in the intersections
   * returned from this function are relative to the local frame of the MeshBVH. When using the
   * `acceleratedRaycast` function as an override for `Mesh.raycast` they are transformed into
   * world space to be consistent with three's results.
   *
   * @param {Ray} ray
   * @param {number|Material|Array<Material>} [materialOrSide=FrontSide]
   * @param {number} [near=0]
   * @param {number} [far=Infinity]
   * @returns {Array<Intersection>}
   */
  raycast(ray, materialOrSide = FrontSide, near = 0, far = 1 / 0) {
    let roots = this._roots, intersects = [], raycastFunc = this.indirect ? raycast_indirect : raycast;
    for (let i = 0, l = roots.length; i < l; i++)
      raycastFunc(this, i, materialOrSide, ray, intersects, near, far);
    return intersects;
  }
  /**
   * Returns the first raycast hit in the model. This is typically much faster than returning
   * all hits. See `raycast` for information on the side and material options as well as the
   * frame of the returned intersections.
   *
   * @param {Ray} ray
   * @param {number|Material|Array<Material>} [materialOrSide=FrontSide]
   * @param {number} [near=0]
   * @param {number} [far=Infinity]
   * @returns {Intersection|null}
   */
  raycastFirst(ray, materialOrSide = FrontSide, near = 0, far = 1 / 0) {
    let roots = this._roots, closestResult = null, raycastFirstFunc = this.indirect ? raycastFirst_indirect : raycastFirst;
    for (let i = 0, l = roots.length; i < l; i++) {
      let result = raycastFirstFunc(this, i, materialOrSide, ray, near, far);
      result != null && (closestResult == null || result.distance < closestResult.distance) && (closestResult = result);
    }
    return closestResult;
  }
  /**
   * Returns whether or not the mesh intersects the given geometry.
   *
   * The `geometryToBvh` parameter is the transform of the geometry in the BVH's local frame.
   *
   * Performance improves considerably if the provided geometry also has a `boundsTree`.
   *
   * @param {BufferGeometry} otherGeometry
   * @param {Matrix4} geometryToBvh - Transform of `otherGeometry` into the local space of
   *   this BVH.
   * @returns {boolean}
   */
  intersectsGeometry(otherGeometry, geomToMesh) {
    let result = !1, roots = this._roots, intersectsGeometryFunc = this.indirect ? intersectsGeometry_indirect : intersectsGeometry;
    for (let i = 0, l = roots.length; i < l && (result = intersectsGeometryFunc(this, i, otherGeometry, geomToMesh), !result); i++)
      ;
    return result;
  }
  /**
   * A generalized cast function that can be used to implement intersection logic for custom
   * shapes. This is used internally for `intersectsBox`, `intersectsSphere`, and more. The
   * function returns as soon as a triangle has been reported as intersected and returns `true`
   * if a triangle has been intersected.
   *
   * @param {Object} callbacks
   * @param {IntersectsBoundsCallback} callbacks.intersectsBounds
   * @param {IntersectsTriangleCallback} [callbacks.intersectsTriangle]
   * @param {IntersectsRangeCallback} [callbacks.intersectsRange]
   * @param {BoundsTraverseOrderCallback} [callbacks.boundsTraverseOrder]
   * @returns {boolean}
   */
  shapecast(callbacks) {
    let triangle4 = ExtendedTrianglePool.getPrimitive(), result = super.shapecast(
      {
        ...callbacks,
        intersectsPrimitive: callbacks.intersectsTriangle,
        scratchPrimitive: triangle4,
        // TODO: is the performance significant enough for the added complexity here?
        // can we just use one function?
        iterate: this.indirect ? iterateOverTriangles_indirect : iterateOverTriangles
      }
    );
    return ExtendedTrianglePool.releasePrimitive(triangle4), result;
  }
  /**
   * A generalized cast function that traverses two BVH structures simultaneously to perform
   * intersection tests between them. This is used internally by `intersectsGeometry`. The
   * function returns `true` as soon as a triangle pair has been reported as intersected by
   * the callbacks.
   *
   * `matrixToLocal` is a Matrix4 that transforms `otherBvh` into the local space of this BVH.
   * The other BVH's triangles are transformed by this matrix before intersection tests.
   *
   * @param {MeshBVH} otherBvh
   * @param {Matrix4} matrixToLocal - Transforms `otherBvh` into the local space of this BVH.
   * @param {Object} callbacks
   * @param {IntersectsRangesCallback} [callbacks.intersectsRanges]
   * @param {IntersectsTrianglesCallback} [callbacks.intersectsTriangles]
   * @returns {boolean}
   */
  bvhcast(otherBvh, matrixToLocal, callbacks) {
    let {
      intersectsRanges,
      intersectsTriangles
    } = callbacks, triangle1 = ExtendedTrianglePool.getPrimitive(), indexAttr1 = this.geometry.index, positionAttr1 = this.geometry.attributes.position, assignTriangle1 = this.indirect ? (i1) => {
      let ti = this.resolveTriangleIndex(i1);
      setTriangle(triangle1, ti * 3, indexAttr1, positionAttr1);
    } : (i1) => {
      setTriangle(triangle1, i1 * 3, indexAttr1, positionAttr1);
    }, triangle23 = ExtendedTrianglePool.getPrimitive(), indexAttr2 = otherBvh.geometry.index, positionAttr2 = otherBvh.geometry.attributes.position, assignTriangle2 = otherBvh.indirect ? (i2) => {
      let ti2 = otherBvh.resolveTriangleIndex(i2);
      setTriangle(triangle23, ti2 * 3, indexAttr2, positionAttr2);
    } : (i2) => {
      setTriangle(triangle23, i2 * 3, indexAttr2, positionAttr2);
    };
    if (intersectsTriangles) {
      if (!(otherBvh instanceof _MeshBVH))
        throw new Error('MeshBVH: "intersectsTriangles" callback can only be used with another MeshBVH.');
      let iterateOverDoubleTriangles = (offset1, count1, offset2, count2, depth1, nodeIndex1, depth2, nodeIndex2) => {
        for (let i2 = offset2, l2 = offset2 + count2; i2 < l2; i2++) {
          assignTriangle2(i2), triangle23.a.applyMatrix4(matrixToLocal), triangle23.b.applyMatrix4(matrixToLocal), triangle23.c.applyMatrix4(matrixToLocal), triangle23.needsUpdate = !0;
          for (let i1 = offset1, l1 = offset1 + count1; i1 < l1; i1++)
            if (assignTriangle1(i1), triangle1.needsUpdate = !0, intersectsTriangles(triangle1, triangle23, i1, i2, depth1, nodeIndex1, depth2, nodeIndex2))
              return !0;
        }
        return !1;
      };
      if (intersectsRanges) {
        let originalIntersectsRanges = intersectsRanges;
        intersectsRanges = function(offset1, count1, offset2, count2, depth1, nodeIndex1, depth2, nodeIndex2) {
          return originalIntersectsRanges(offset1, count1, offset2, count2, depth1, nodeIndex1, depth2, nodeIndex2) ? !0 : iterateOverDoubleTriangles(offset1, count1, offset2, count2, depth1, nodeIndex1, depth2, nodeIndex2);
        };
      } else
        intersectsRanges = iterateOverDoubleTriangles;
    }
    return super.bvhcast(otherBvh, matrixToLocal, { intersectsRanges });
  }
  /* Derived Cast Functions */
  /**
   * Returns whether or not the mesh intersects the given box.
   *
   * The `boxToBvh` parameter is the transform of the box in the meshes frame.
   *
   * @param {Box3} box
   * @param {Matrix4} boxToBvh - Transform of the box in the local space of this BVH.
   * @returns {boolean}
   */
  intersectsBox(box, boxToMesh) {
    return _obb.set(box.min, box.max, boxToMesh), _obb.needsUpdate = !0, this.shapecast(
      {
        intersectsBounds: (box2) => _obb.intersectsBox(box2),
        intersectsTriangle: (tri) => _obb.intersectsTriangle(tri)
      }
    );
  }
  /**
   * Returns whether or not the mesh intersects the given sphere.
   *
   * @param {Sphere} sphere
   * @returns {boolean}
   */
  intersectsSphere(sphere) {
    return this.shapecast(
      {
        intersectsBounds: (box) => sphere.intersectsBox(box),
        intersectsTriangle: (tri) => tri.intersectsSphere(sphere)
      }
    );
  }
  /**
   * Computes the closest distance from the geometry to the mesh and puts the closest point on
   * the mesh in `target1` (in the frame of the BVH) and the closest point on the other
   * geometry in `target2` (in the geometry frame). If `target1` is not provided a new Object
   * is created and returned from the function.
   *
   * The `geometryToBvh` parameter is the transform of the geometry in the BVH's local frame.
   *
   * If a point is found that is closer than `minThreshold` then the function will return that
   * result early. Any triangles or points outside of `maxThreshold` are ignored. If no point
   * is found within the min / max thresholds then `null` is returned and the target objects
   * are not modified.
   *
   * The returned faceIndex in `target1` and `target2` can be used with the standalone function
   * `getTriangleHitPointInfo` to obtain more information like UV coordinates, triangle normal
   * and materialIndex.
   *
   * @note This function can be very slow if `geometry` does not have a `geometry.boundsTree`
   * computed.
   *
   * @param {BufferGeometry} otherGeometry
   * @param {Matrix4} geometryToBvh - Transform of `otherGeometry` into the local space of
   *   this BVH.
   * @param {HitPointInfo} [target1={}]
   * @param {HitPointInfo} [target2={}]
   * @param {number} [minThreshold=0]
   * @param {number} [maxThreshold=Infinity]
   * @returns {HitPointInfo|null}
   */
  closestPointToGeometry(otherGeometry, geometryToBvh, target1 = {}, target2 = {}, minThreshold = 0, maxThreshold = 1 / 0) {
    return (this.indirect ? closestPointToGeometry_indirect : closestPointToGeometry)(
      this,
      otherGeometry,
      geometryToBvh,
      target1,
      target2,
      minThreshold,
      maxThreshold
    );
  }
  /**
   * Computes the closest distance from the point to the mesh and gives additional information
   * in `target`. The target can be left undefined to default to a new object which is
   * ultimately returned by the function.
   *
   * If a point is found that is closer than `minThreshold` then the function will return that
   * result early. Any triangles or points outside of `maxThreshold` are ignored. If no point
   * is found within the min / max thresholds then `null` is returned and the `target` object
   * is not modified.
   *
   * The returned faceIndex can be used with the standalone function `getTriangleHitPointInfo`
   * to obtain more information like UV coordinates, triangle normal and materialIndex.
   *
   * @param {Vector3} point
   * @param {HitPointInfo} [target={}]
   * @param {number} [minThreshold=0]
   * @param {number} [maxThreshold=Infinity]
   * @returns {HitPointInfo|null}
   */
  closestPointToPoint(point, target2 = {}, minThreshold = 0, maxThreshold = 1 / 0) {
    return closestPointToPoint(
      this,
      point,
      target2,
      minThreshold,
      maxThreshold
    );
  }
};

// npm/three-mesh-bvh/src/webgl/MeshBVHUniformStruct.js
import {
  DataTexture as DataTexture2,
  FloatType as FloatType2,
  UnsignedIntType as UnsignedIntType2,
  RGBAFormat as RGBAFormat2,
  RGIntegerFormat as RGIntegerFormat2,
  NearestFilter as NearestFilter2,
  BufferAttribute as BufferAttribute3
} from "../three/build/three.module.js";

// npm/three-mesh-bvh/src/webgl/VertexAttributeTexture.js
import {
  DataTexture,
  FloatType,
  IntType,
  UnsignedIntType,
  ByteType,
  UnsignedByteType,
  ShortType,
  UnsignedShortType,
  RedFormat,
  RGFormat,
  RGBAFormat,
  RedIntegerFormat,
  RGIntegerFormat,
  RGBAIntegerFormat,
  NearestFilter
} from "../three/build/three.module.js";
function countToStringFormat(count) {
  switch (count) {
    case 1:
      return "R";
    case 2:
      return "RG";
    case 3:
      return "RGBA";
    case 4:
      return "RGBA";
  }
  throw new Error();
}
function countToFormat(count) {
  switch (count) {
    case 1:
      return RedFormat;
    case 2:
      return RGFormat;
    case 3:
      return RGBAFormat;
    case 4:
      return RGBAFormat;
  }
}
function countToIntFormat(count) {
  switch (count) {
    case 1:
      return RedIntegerFormat;
    case 2:
      return RGIntegerFormat;
    case 3:
      return RGBAIntegerFormat;
    case 4:
      return RGBAIntegerFormat;
  }
}
var VertexAttributeTexture = class extends DataTexture {
  constructor() {
    super(), this.minFilter = NearestFilter, this.magFilter = NearestFilter, this.generateMipmaps = !1, this.overrideItemSize = null, this._forcedType = null;
  }
  /**
   * Updates the texture to have the data contained in the passed BufferAttribute using the
   * BufferAttribute `itemSize` field, `normalized` field, and TypedArray layout to determine
   * the appropriate texture layout, format, and type. The texture dimensions will always be
   * square. Because these are intended to be sampled as 1D arrays the width of the texture must
   * be taken into account to derive a sampling uv. See `texelFetch1D` in shaderFunctions.
   *
   * @param {BufferAttribute} attribute
   * @returns {void}
   */
  updateFrom(attr) {
    let overrideItemSize = this.overrideItemSize, originalItemSize = attr.itemSize, originalCount = attr.count;
    if (overrideItemSize !== null) {
      if (originalItemSize * originalCount % overrideItemSize !== 0)
        throw new Error("VertexAttributeTexture: overrideItemSize must divide evenly into buffer length.");
      attr.itemSize = overrideItemSize, attr.count = originalCount * originalItemSize / overrideItemSize;
    }
    let itemSize = attr.itemSize, count = attr.count, normalized = attr.normalized, originalBufferCons = attr.array.constructor, byteCount = originalBufferCons.BYTES_PER_ELEMENT, targetType = this._forcedType, finalStride = itemSize;
    if (targetType === null)
      switch (originalBufferCons) {
        case Float32Array:
          targetType = FloatType;
          break;
        case Uint8Array:
        case Uint16Array:
        case Uint32Array:
          targetType = UnsignedIntType;
          break;
        case Int8Array:
        case Int16Array:
        case Int32Array:
          targetType = IntType;
          break;
      }
    let type, format, normalizeValue, targetBufferCons, internalFormat = countToStringFormat(itemSize);
    switch (targetType) {
      case FloatType:
        normalizeValue = 1, format = countToFormat(itemSize), normalized && byteCount === 1 ? (targetBufferCons = originalBufferCons, internalFormat += "8", originalBufferCons === Uint8Array ? type = UnsignedByteType : (type = ByteType, internalFormat += "_SNORM")) : (targetBufferCons = Float32Array, internalFormat += "32F", type = FloatType);
        break;
      case IntType:
        internalFormat += byteCount * 8 + "I", normalizeValue = normalized ? Math.pow(2, originalBufferCons.BYTES_PER_ELEMENT * 8 - 1) : 1, format = countToIntFormat(itemSize), byteCount === 1 ? (targetBufferCons = Int8Array, type = ByteType) : byteCount === 2 ? (targetBufferCons = Int16Array, type = ShortType) : (targetBufferCons = Int32Array, type = IntType);
        break;
      case UnsignedIntType:
        internalFormat += byteCount * 8 + "UI", normalizeValue = normalized ? Math.pow(2, originalBufferCons.BYTES_PER_ELEMENT * 8 - 1) : 1, format = countToIntFormat(itemSize), byteCount === 1 ? (targetBufferCons = Uint8Array, type = UnsignedByteType) : byteCount === 2 ? (targetBufferCons = Uint16Array, type = UnsignedShortType) : (targetBufferCons = Uint32Array, type = UnsignedIntType);
        break;
    }
    finalStride === 3 && (format === RGBAFormat || format === RGBAIntegerFormat) && (finalStride = 4);
    let dimension = Math.ceil(Math.sqrt(count)) || 1, length = finalStride * dimension * dimension, dataArray = new targetBufferCons(length), originalNormalized = attr.normalized;
    attr.normalized = !1;
    for (let i = 0; i < count; i++) {
      let ii = finalStride * i;
      dataArray[ii] = attr.getX(i) / normalizeValue, itemSize >= 2 && (dataArray[ii + 1] = attr.getY(i) / normalizeValue), itemSize >= 3 && (dataArray[ii + 2] = attr.getZ(i) / normalizeValue, finalStride === 4 && (dataArray[ii + 3] = 1)), itemSize >= 4 && (dataArray[ii + 3] = attr.getW(i) / normalizeValue);
    }
    attr.normalized = originalNormalized, this.internalFormat = internalFormat, this.format = format, this.type = type, this.image.width = dimension, this.image.height = dimension, this.image.data = dataArray, this.needsUpdate = !0, this.dispose(), attr.itemSize = originalItemSize, attr.count = originalCount;
  }
}, UIntVertexAttributeTexture = class extends VertexAttributeTexture {
  constructor() {
    super(), this._forcedType = UnsignedIntType;
  }
};
var FloatVertexAttributeTexture = class extends VertexAttributeTexture {
  constructor() {
    super(), this._forcedType = FloatType;
  }
};

// npm/three-mesh-bvh/src/webgl/MeshBVHUniformStruct.js
var MeshBVHUniformStruct = class {
  constructor() {
    this.index = new UIntVertexAttributeTexture(), this.position = new FloatVertexAttributeTexture(), this.bvhBounds = new DataTexture2(), this.bvhContents = new DataTexture2(), this._cachedIndexAttr = null, this.index.overrideItemSize = 3;
  }
  /**
   * Updates the object and associated textures with data from the provided BVH.
   *
   * @param {MeshBVH} bvh
   * @returns {void}
   */
  updateFrom(bvh) {
    let { geometry } = bvh;
    if (bvhToTextures(bvh, this.bvhBounds, this.bvhContents), this.position.updateFrom(geometry.attributes.position), bvh.indirect) {
      let indirectBuffer = bvh._indirectBuffer;
      if (this._cachedIndexAttr === null || this._cachedIndexAttr.count !== indirectBuffer.length)
        if (geometry.index)
          this._cachedIndexAttr = geometry.index.clone();
        else {
          let array = getIndexArray(getVertexCount(geometry));
          this._cachedIndexAttr = new BufferAttribute3(array, 1, !1);
        }
      dereferenceIndex(geometry, indirectBuffer, this._cachedIndexAttr), this.index.updateFrom(this._cachedIndexAttr);
    } else
      this.index.updateFrom(geometry.index);
  }
  /**
   * Dispose of the associated textures.
   *
   * @returns {void}
   */
  dispose() {
    let { index, position, bvhBounds, bvhContents } = this;
    index && index.dispose(), position && position.dispose(), bvhBounds && bvhBounds.dispose(), bvhContents && bvhContents.dispose();
  }
};
function dereferenceIndex(geometry, indirectBuffer, target2) {
  let unpacked = target2.array, indexArray = geometry.index ? geometry.index.array : null;
  for (let i = 0, l = indirectBuffer.length; i < l; i++) {
    let i3 = 3 * i, v3 = 3 * indirectBuffer[i];
    for (let c = 0; c < 3; c++)
      unpacked[i3 + c] = indexArray ? indexArray[v3 + c] : v3 + c;
  }
}
function bvhToTextures(bvh, boundsTexture, contentsTexture) {
  let roots = bvh._roots;
  if (roots.length !== 1)
    throw new Error("MeshBVHUniformStruct: Multi-root BVHs not supported.");
  let root = roots[0], uint16Array = new Uint16Array(root), uint32Array = new Uint32Array(root), float32Array = new Float32Array(root), nodeCount = root.byteLength / 32, boundsDimension = 2 * Math.ceil(Math.sqrt(nodeCount / 2)), boundsArray = new Float32Array(4 * boundsDimension * boundsDimension), contentsDimension = Math.ceil(Math.sqrt(nodeCount)), contentsArray = new Uint32Array(2 * contentsDimension * contentsDimension);
  for (let i = 0; i < nodeCount; i++) {
    let nodeIndex32 = i * 32 / 4, nodeIndex16 = nodeIndex32 * 2, boundsIndex = nodeIndex32;
    for (let b = 0; b < 3; b++)
      boundsArray[8 * i + 0 + b] = float32Array[boundsIndex + 0 + b], boundsArray[8 * i + 4 + b] = float32Array[boundsIndex + 3 + b];
    if (IS_LEAF(nodeIndex16, uint16Array)) {
      let count = COUNT(nodeIndex16, uint16Array), offset = OFFSET(nodeIndex32, uint32Array), mergedLeafCount = -65536 | count;
      contentsArray[i * 2 + 0] = mergedLeafCount, contentsArray[i * 2 + 1] = offset;
    } else {
      let rightNodeIndex = uint32Array[nodeIndex32 + 6], splitAxis = SPLIT_AXIS(nodeIndex32, uint32Array);
      contentsArray[i * 2 + 0] = splitAxis, contentsArray[i * 2 + 1] = rightNodeIndex;
    }
  }
  boundsTexture.image.data = boundsArray, boundsTexture.image.width = boundsDimension, boundsTexture.image.height = boundsDimension, boundsTexture.format = RGBAFormat2, boundsTexture.type = FloatType2, boundsTexture.internalFormat = "RGBA32F", boundsTexture.minFilter = NearestFilter2, boundsTexture.magFilter = NearestFilter2, boundsTexture.generateMipmaps = !1, boundsTexture.needsUpdate = !0, boundsTexture.dispose(), contentsTexture.image.data = contentsArray, contentsTexture.image.width = contentsDimension, contentsTexture.image.height = contentsDimension, contentsTexture.format = RGIntegerFormat2, contentsTexture.type = UnsignedIntType2, contentsTexture.internalFormat = "RG32UI", contentsTexture.minFilter = NearestFilter2, contentsTexture.magFilter = NearestFilter2, contentsTexture.generateMipmaps = !1, contentsTexture.needsUpdate = !0, contentsTexture.dispose();
}

// npm/three-mesh-bvh/src/webgl/BVHShaderGLSL.js
var BVHShaderGLSL_exports = {};
__export(BVHShaderGLSL_exports, {
  bvh_distance_functions: () => bvh_distance_functions,
  bvh_ray_functions: () => bvh_ray_functions,
  bvh_struct_definitions: () => bvh_struct_definitions,
  common_functions: () => common_functions
});

// npm/three-mesh-bvh/src/webgl/glsl/common_functions.glsl.js
var common_functions = (
  /* glsl */
  `

// A stack of uint32 indices can can store the indices for
// a perfectly balanced tree with a depth up to 31. Lower stack
// depth gets higher performance.
//
// However not all trees are balanced. Best value to set this to
// is the trees max depth.
#ifndef BVH_STACK_DEPTH
#define BVH_STACK_DEPTH 60
#endif

#ifndef INFINITY
#define INFINITY 1e20
#endif

// Utilities
uvec4 uTexelFetch1D( usampler2D tex, uint index ) {

	uint width = uint( textureSize( tex, 0 ).x );
	uvec2 uv;
	uv.x = index % width;
	uv.y = index / width;

	return texelFetch( tex, ivec2( uv ), 0 );

}

ivec4 iTexelFetch1D( isampler2D tex, uint index ) {

	uint width = uint( textureSize( tex, 0 ).x );
	uvec2 uv;
	uv.x = index % width;
	uv.y = index / width;

	return texelFetch( tex, ivec2( uv ), 0 );

}

vec4 texelFetch1D( sampler2D tex, uint index ) {

	uint width = uint( textureSize( tex, 0 ).x );
	uvec2 uv;
	uv.x = index % width;
	uv.y = index / width;

	return texelFetch( tex, ivec2( uv ), 0 );

}

vec4 textureSampleBarycoord( sampler2D tex, vec3 barycoord, uvec3 faceIndices ) {

	return
		barycoord.x * texelFetch1D( tex, faceIndices.x ) +
		barycoord.y * texelFetch1D( tex, faceIndices.y ) +
		barycoord.z * texelFetch1D( tex, faceIndices.z );

}

void ndcToCameraRay(
	vec2 coord, mat4 cameraWorld, mat4 invProjectionMatrix,
	out vec3 rayOrigin, out vec3 rayDirection
) {

	// get camera look direction and near plane for camera clipping
	vec4 lookDirection = cameraWorld * vec4( 0.0, 0.0, - 1.0, 0.0 );
	vec4 nearVector = invProjectionMatrix * vec4( 0.0, 0.0, - 1.0, 1.0 );
	float near = abs( nearVector.z / nearVector.w );

	// get the camera direction and position from camera matrices
	vec4 origin = cameraWorld * vec4( 0.0, 0.0, 0.0, 1.0 );
	vec4 direction = invProjectionMatrix * vec4( coord, 0.5, 1.0 );
	direction /= direction.w;
	direction = cameraWorld * direction - origin;

	// slide the origin along the ray until it sits at the near clip plane position
	origin.xyz += direction.xyz * near / dot( direction, lookDirection );

	rayOrigin = origin.xyz;
	rayDirection = direction.xyz;

}
`
);

// npm/three-mesh-bvh/src/webgl/glsl/bvh_distance_functions.glsl.js
var bvh_distance_functions = (
  /* glsl */
  `

float dot2( vec3 v ) {

	return dot( v, v );

}

// implementation from https://www.shadertoy.com/view/ttfGWl, though method 2 has been removed
// and is now available at this fork: https://www.shadertoy.com/view/WlB3zW
vec3 closestPointToTriangle( vec3 p, vec3 v0, vec3 v1, vec3 v2, out vec3 barycoord ) {

    vec3 v10 = v1 - v0;
    vec3 v21 = v2 - v1;
    vec3 v02 = v0 - v2;

	vec3 p0 = p - v0;
	vec3 p1 = p - v1;
	vec3 p2 = p - v2;

    vec3 nor = cross( v10, v02 );

    // method 2, in barycentric space
    vec3  q = cross( nor, p0 );
    float d = 1.0 / dot2( nor );
    float u = d * dot( q, v02 );
    float v = d * dot( q, v10 );
    float w = 1.0 - u - v;

	if( u < 0.0 ) {

		w = clamp( dot( p2, v02 ) / dot2( v02 ), 0.0, 1.0 );
		u = 0.0;
		v = 1.0 - w;

	} else if( v < 0.0 ) {

		u = clamp( dot( p0, v10 ) / dot2( v10 ), 0.0, 1.0 );
		v = 0.0;
		w = 1.0 - u;

	} else if( w < 0.0 ) {

		v = clamp( dot( p1, v21 ) / dot2( v21 ), 0.0, 1.0 );
		w = 0.0;
		u = 1.0 - v;

	}

	// output the barycoord in v0, v1, v2 weight order
	barycoord = vec3( w, u, v );
    return u * v1 + v * v2 + w * v0;

}

float distanceToTriangles(
	// geometry info and triangle range
	sampler2D positionAttr, usampler2D indexAttr, uint offset, uint count,

	// point and cut off range
	vec3 point, float closestDistanceSquared,

	// outputs
	inout uvec4 faceIndices, inout vec3 faceNormal, inout vec3 barycoord, inout float side, inout vec3 outPoint
) {

	bool found = false;
	vec3 localBarycoord;
	for ( uint i = offset, l = offset + count; i < l; i ++ ) {

		uvec3 indices = uTexelFetch1D( indexAttr, i ).xyz;
		vec3 a = texelFetch1D( positionAttr, indices.x ).rgb;
		vec3 b = texelFetch1D( positionAttr, indices.y ).rgb;
		vec3 c = texelFetch1D( positionAttr, indices.z ).rgb;

		// get the closest point and barycoord
		vec3 closestPoint = closestPointToTriangle( point, a, b, c, localBarycoord );
		vec3 delta = point - closestPoint;
		float sqDist = dot2( delta );
		if ( sqDist < closestDistanceSquared ) {

			// set the output results
			closestDistanceSquared = sqDist;
			faceIndices = uvec4( indices.xyz, i );
			faceNormal = normalize( cross( a - b, b - c ) );
			barycoord = localBarycoord;
			outPoint = closestPoint;
			side = sign( dot( faceNormal, delta ) );

		}

	}

	return closestDistanceSquared;

}

float distanceSqToBounds( vec3 point, vec3 boundsMin, vec3 boundsMax ) {

	vec3 clampedPoint = clamp( point, boundsMin, boundsMax );
	vec3 delta = point - clampedPoint;
	return dot( delta, delta );

}

float distanceSqToBVHNodeBoundsPoint( vec3 point, sampler2D bvhBounds, uint currNodeIndex ) {

	uint cni2 = currNodeIndex * 2u;
	vec3 boundsMin = texelFetch1D( bvhBounds, cni2 ).xyz;
	vec3 boundsMax = texelFetch1D( bvhBounds, cni2 + 1u ).xyz;
	return distanceSqToBounds( point, boundsMin, boundsMax );

}

// use a macro to hide the fact that we need to expand the struct into separate fields
#define	bvhClosestPointToPoint(		bvh,		point, maxDistance, faceIndices, faceNormal, barycoord, side, outPoint	)	_bvhClosestPointToPoint(		bvh.position, bvh.index, bvh.bvhBounds, bvh.bvhContents,		point, maxDistance, faceIndices, faceNormal, barycoord, side, outPoint	)

float _bvhClosestPointToPoint(
	// bvh info
	sampler2D bvh_position, usampler2D bvh_index, sampler2D bvh_bvhBounds, usampler2D bvh_bvhContents,

	// point to check
	vec3 point, float maxDistance,

	// output variables
	inout uvec4 faceIndices, inout vec3 faceNormal, inout vec3 barycoord,
	inout float side, inout vec3 outPoint
 ) {

	// stack needs to be twice as long as the deepest tree we expect because
	// we push both the left and right child onto the stack every traversal
	int pointer = 0;
	uint stack[ BVH_STACK_DEPTH ];
	stack[ 0 ] = 0u;

	float closestDistanceSquared = maxDistance * maxDistance;
	bool found = false;
	while ( pointer > - 1 && pointer < BVH_STACK_DEPTH ) {

		uint currNodeIndex = stack[ pointer ];
		pointer --;

		// check if we intersect the current bounds
		float boundsHitDistance = distanceSqToBVHNodeBoundsPoint( point, bvh_bvhBounds, currNodeIndex );
		if ( boundsHitDistance > closestDistanceSquared ) {

			continue;

		}

		uvec2 boundsInfo = uTexelFetch1D( bvh_bvhContents, currNodeIndex ).xy;
		bool isLeaf = bool( boundsInfo.x & 0xffff0000u );
		if ( isLeaf ) {

			uint count = boundsInfo.x & 0x0000ffffu;
			uint offset = boundsInfo.y;
			closestDistanceSquared = distanceToTriangles(
				bvh_position, bvh_index, offset, count, point, closestDistanceSquared,

				// outputs
				faceIndices, faceNormal, barycoord, side, outPoint
			);

		} else {

			uint leftIndex = currNodeIndex + 1u;
			uint splitAxis = boundsInfo.x & 0x0000ffffu;
			uint rightIndex = currNodeIndex + boundsInfo.y;
			bool leftToRight = distanceSqToBVHNodeBoundsPoint( point, bvh_bvhBounds, leftIndex ) < distanceSqToBVHNodeBoundsPoint( point, bvh_bvhBounds, rightIndex );//rayDirection[ splitAxis ] >= 0.0;
			uint c1 = leftToRight ? leftIndex : rightIndex;
			uint c2 = leftToRight ? rightIndex : leftIndex;

			// set c2 in the stack so we traverse it later. We need to keep track of a pointer in
			// the stack while we traverse. The second pointer added is the one that will be
			// traversed first
			pointer ++;
			stack[ pointer ] = c2;
			pointer ++;
			stack[ pointer ] = c1;

		}

	}

	return sqrt( closestDistanceSquared );

}
`
);

// npm/three-mesh-bvh/src/webgl/glsl/bvh_ray_functions.glsl.js
var bvh_ray_functions = (
  /* glsl */
  `

#ifndef TRI_INTERSECT_EPSILON
#define TRI_INTERSECT_EPSILON 1e-5
#endif

// Raycasting
bool intersectsBounds( vec3 rayOrigin, vec3 rayDirection, vec3 boundsMin, vec3 boundsMax, out float dist ) {

	// https://www.reddit.com/r/opengl/comments/8ntzz5/fast_glsl_ray_box_intersection/
	// https://tavianator.com/2011/ray_box.html
	vec3 invDir = 1.0 / rayDirection;

	// find intersection distances for each plane
	vec3 tMinPlane = invDir * ( boundsMin - rayOrigin );
	vec3 tMaxPlane = invDir * ( boundsMax - rayOrigin );

	// get the min and max distances from each intersection
	vec3 tMinHit = min( tMaxPlane, tMinPlane );
	vec3 tMaxHit = max( tMaxPlane, tMinPlane );

	// get the furthest hit distance
	vec2 t = max( tMinHit.xx, tMinHit.yz );
	float t0 = max( t.x, t.y );

	// get the minimum hit distance
	t = min( tMaxHit.xx, tMaxHit.yz );
	float t1 = min( t.x, t.y );

	// set distance to 0.0 if the ray starts inside the box
	dist = max( t0, 0.0 );

	return t1 >= dist;

}

bool intersectsTriangle(
	vec3 rayOrigin, vec3 rayDirection, vec3 a, vec3 b, vec3 c,
	out vec3 barycoord, out vec3 norm, out float dist, out float side
) {

	// https://stackoverflow.com/questions/42740765/intersection-between-line-and-triangle-in-3d
	vec3 edge1 = b - a;
	vec3 edge2 = c - a;
	norm = cross( edge1, edge2 );

	float det = - dot( rayDirection, norm );
	float invdet = 1.0 / det;

	vec3 AO = rayOrigin - a;
	vec3 DAO = cross( AO, rayDirection );

	vec4 uvt;
	uvt.x = dot( edge2, DAO ) * invdet;
	uvt.y = - dot( edge1, DAO ) * invdet;
	uvt.z = dot( AO, norm ) * invdet;
	uvt.w = 1.0 - uvt.x - uvt.y;

	// set the hit information
	barycoord = uvt.wxy; // arranged in A, B, C order
	dist = uvt.z;
	side = sign( det );
	norm = side * normalize( norm );

	// add an epsilon to avoid misses between triangles
	uvt += vec4( TRI_INTERSECT_EPSILON );

	return all( greaterThanEqual( uvt, vec4( 0.0 ) ) );

}

bool intersectTriangles(
	// geometry info and triangle range
	sampler2D positionAttr, usampler2D indexAttr, uint offset, uint count,

	// ray
	vec3 rayOrigin, vec3 rayDirection,

	// outputs
	inout float minDistance, inout uvec4 faceIndices, inout vec3 faceNormal, inout vec3 barycoord,
	inout float side, inout float dist
) {

	bool found = false;
	vec3 localBarycoord, localNormal;
	float localDist, localSide;
	for ( uint i = offset, l = offset + count; i < l; i ++ ) {

		uvec3 indices = uTexelFetch1D( indexAttr, i ).xyz;
		vec3 a = texelFetch1D( positionAttr, indices.x ).rgb;
		vec3 b = texelFetch1D( positionAttr, indices.y ).rgb;
		vec3 c = texelFetch1D( positionAttr, indices.z ).rgb;

		if (
			intersectsTriangle( rayOrigin, rayDirection, a, b, c, localBarycoord, localNormal, localDist, localSide )
			&& localDist < minDistance
		) {

			found = true;
			minDistance = localDist;

			faceIndices = uvec4( indices.xyz, i );
			faceNormal = localNormal;

			side = localSide;
			barycoord = localBarycoord;
			dist = localDist;

		}

	}

	return found;

}

bool intersectsBVHNodeBounds( vec3 rayOrigin, vec3 rayDirection, sampler2D bvhBounds, uint currNodeIndex, out float dist ) {

	uint cni2 = currNodeIndex * 2u;
	vec3 boundsMin = texelFetch1D( bvhBounds, cni2 ).xyz;
	vec3 boundsMax = texelFetch1D( bvhBounds, cni2 + 1u ).xyz;
	return intersectsBounds( rayOrigin, rayDirection, boundsMin, boundsMax, dist );

}

// use a macro to hide the fact that we need to expand the struct into separate fields
#define	bvhIntersectFirstHit(		bvh,		rayOrigin, rayDirection, faceIndices, faceNormal, barycoord, side, dist	)	_bvhIntersectFirstHit(		bvh.position, bvh.index, bvh.bvhBounds, bvh.bvhContents,		rayOrigin, rayDirection, faceIndices, faceNormal, barycoord, side, dist	)

bool _bvhIntersectFirstHit(
	// bvh info
	sampler2D bvh_position, usampler2D bvh_index, sampler2D bvh_bvhBounds, usampler2D bvh_bvhContents,

	// ray
	vec3 rayOrigin, vec3 rayDirection,

	// output variables split into separate variables due to output precision
	inout uvec4 faceIndices, inout vec3 faceNormal, inout vec3 barycoord,
	inout float side, inout float dist
) {

	// stack needs to be twice as long as the deepest tree we expect because
	// we push both the left and right child onto the stack every traversal
	int pointer = 0;
	uint stack[ BVH_STACK_DEPTH ];
	stack[ 0 ] = 0u;

	float triangleDistance = INFINITY;
	bool found = false;
	while ( pointer > - 1 && pointer < BVH_STACK_DEPTH ) {

		uint currNodeIndex = stack[ pointer ];
		pointer --;

		// check if we intersect the current bounds
		float boundsHitDistance;
		if (
			! intersectsBVHNodeBounds( rayOrigin, rayDirection, bvh_bvhBounds, currNodeIndex, boundsHitDistance )
			|| boundsHitDistance > triangleDistance
		) {

			continue;

		}

		uvec2 boundsInfo = uTexelFetch1D( bvh_bvhContents, currNodeIndex ).xy;
		bool isLeaf = bool( boundsInfo.x & 0xffff0000u );

		if ( isLeaf ) {

			uint count = boundsInfo.x & 0x0000ffffu;
			uint offset = boundsInfo.y;

			found = intersectTriangles(
				bvh_position, bvh_index, offset, count,
				rayOrigin, rayDirection, triangleDistance,
				faceIndices, faceNormal, barycoord, side, dist
			) || found;

		} else {

			uint leftIndex = currNodeIndex + 1u;
			uint splitAxis = boundsInfo.x & 0x0000ffffu;
			uint rightIndex = currNodeIndex + boundsInfo.y;

			bool leftToRight = rayDirection[ splitAxis ] >= 0.0;
			uint c1 = leftToRight ? leftIndex : rightIndex;
			uint c2 = leftToRight ? rightIndex : leftIndex;

			// set c2 in the stack so we traverse it later. We need to keep track of a pointer in
			// the stack while we traverse. The second pointer added is the one that will be
			// traversed first
			pointer ++;
			stack[ pointer ] = c2;

			pointer ++;
			stack[ pointer ] = c1;

		}

	}

	return found;

}
`
);

// npm/three-mesh-bvh/src/webgl/glsl/bvh_struct_definitions.glsl.js
var bvh_struct_definitions = (
  /* glsl */
  `
struct BVH {

	usampler2D index;
	sampler2D position;

	sampler2D bvhBounds;
	usampler2D bvhContents;

};
`
);

// npm/three-mesh-bvh/src/index.js
var shaderIntersectFunction = `
	${common_functions}
	${bvh_ray_functions}
`;

// npm/three-gpu-pathtracer/src/core/utils/StaticGeometryGenerator.js
import { BufferAttribute as BufferAttribute7, BufferGeometry as BufferGeometry4, Mesh, MeshBasicMaterial } from "../three/build/three.module.js";

// npm/three-gpu-pathtracer/src/core/utils/mergeGeometries.js
import { BufferAttribute as BufferAttribute5, BufferGeometry } from "../three/build/three.module.js";

// npm/three-gpu-pathtracer/src/core/utils/BufferAttributeUtils.js
import { BufferAttribute as BufferAttribute4 } from "../three/build/three.module.js";
function copyAttributeContents(attr, target2, targetOffset = 0) {
  if (attr.isInterleavedBufferAttribute) {
    let itemSize = attr.itemSize;
    for (let i = 0, l = attr.count; i < l; i++) {
      let io = i + targetOffset;
      target2.setX(io, attr.getX(i)), itemSize >= 2 && target2.setY(io, attr.getY(i)), itemSize >= 3 && target2.setZ(io, attr.getZ(i)), itemSize >= 4 && target2.setW(io, attr.getW(i));
    }
  } else {
    let array = target2.array, cons = array.constructor, byteOffset = array.BYTES_PER_ELEMENT * attr.itemSize * targetOffset;
    new cons(array.buffer, byteOffset, attr.array.length).set(attr.array);
  }
}
function createAttributeClone(attr, countOverride = null) {
  let cons = attr.array.constructor, normalized = attr.normalized, itemSize = attr.itemSize, count = countOverride === null ? attr.count : countOverride;
  return new BufferAttribute4(new cons(itemSize * count), itemSize, normalized);
}
function validateAttributes(attr1, attr2) {
  if (!attr1 && !attr2)
    return !0;
  if (!!attr1 != !!attr2)
    return !1;
  let sameCount = attr1.count === attr2.count, sameNormalized = attr1.normalized === attr2.normalized, sameType = attr1.array.constructor === attr2.array.constructor, sameItemSize = attr1.itemSize === attr2.itemSize;
  return !(!sameCount || !sameNormalized || !sameType || !sameItemSize);
}

// npm/three-gpu-pathtracer/src/core/utils/mergeGeometries.js
function validateMergeability(geometries) {
  let isIndexed = geometries[0].index !== null, attributesUsed = new Set(Object.keys(geometries[0].attributes));
  if (!geometries[0].getAttribute("position"))
    throw new Error("StaticGeometryGenerator: position attribute is required.");
  for (let i = 0; i < geometries.length; ++i) {
    let geometry = geometries[i], attributesCount = 0;
    if (isIndexed !== (geometry.index !== null))
      throw new Error("StaticGeometryGenerator: All geometries must have compatible attributes; make sure index attribute exists among all geometries, or in none of them.");
    for (let name in geometry.attributes) {
      if (!attributesUsed.has(name))
        throw new Error('StaticGeometryGenerator: All geometries must have compatible attributes; make sure "' + name + '" attribute exists among all geometries, or in none of them.');
      attributesCount++;
    }
    if (attributesCount !== attributesUsed.size)
      throw new Error("StaticGeometryGenerator: All geometries must have the same number of attributes.");
  }
}
function getTotalIndexCount(geometries) {
  let result = 0;
  for (let i = 0, l = geometries.length; i < l; i++)
    result += geometries[i].getIndex().count;
  return result;
}
function getTotalAttributeCount(geometries) {
  let result = 0;
  for (let i = 0, l = geometries.length; i < l; i++)
    result += geometries[i].getAttribute("position").count;
  return result;
}
function trimMismatchedAttributes(target2, indexCount, attrCount) {
  target2.index && target2.index.count !== indexCount && target2.setIndex(null);
  let attributes = target2.attributes;
  for (let key in attributes)
    attributes[key].count !== attrCount && target2.deleteAttribute(key);
}
function mergeGeometries(geometries, options = {}, targetGeometry = new BufferGeometry()) {
  let {
    useGroups = !1,
    forceUpdate = !1,
    skipAssigningAttributes = [],
    overwriteIndex = !0
  } = options;
  validateMergeability(geometries);
  let isIndexed = geometries[0].index !== null, totalIndexCount = isIndexed ? getTotalIndexCount(geometries) : -1, totalAttributeCount = getTotalAttributeCount(geometries);
  if (trimMismatchedAttributes(targetGeometry, totalIndexCount, totalAttributeCount), useGroups) {
    let offset = 0;
    for (let i = 0, l = geometries.length; i < l; i++) {
      let geometry = geometries[i], primitiveCount;
      isIndexed ? primitiveCount = geometry.getIndex().count : primitiveCount = geometry.getAttribute("position").count, targetGeometry.addGroup(offset, primitiveCount, i), offset += primitiveCount;
    }
  }
  if (isIndexed) {
    let forceUpdateIndex = !1;
    if (targetGeometry.index || (targetGeometry.setIndex(new BufferAttribute5(new Uint32Array(totalIndexCount), 1, !1)), forceUpdateIndex = !0), forceUpdateIndex || overwriteIndex) {
      let targetOffset = 0, indexOffset = 0, targetIndex = targetGeometry.getIndex();
      for (let i = 0, l = geometries.length; i < l; i++) {
        let geometry = geometries[i], index = geometry.getIndex();
        if (!(!forceUpdate && !forceUpdateIndex && skipAssigningAttributes[i]))
          for (let j = 0; j < index.count; ++j)
            targetIndex.setX(targetOffset + j, index.getX(j) + indexOffset);
        targetOffset += index.count, indexOffset += geometry.getAttribute("position").count;
      }
    }
  }
  let attributes = Object.keys(geometries[0].attributes);
  for (let i = 0, l = attributes.length; i < l; i++) {
    let forceUpdateAttr = !1, key = attributes[i];
    if (!targetGeometry.getAttribute(key)) {
      let firstAttr = geometries[0].getAttribute(key);
      targetGeometry.setAttribute(key, createAttributeClone(firstAttr, totalAttributeCount)), forceUpdateAttr = !0;
    }
    let offset = 0, targetAttribute = targetGeometry.getAttribute(key);
    for (let g = 0, l2 = geometries.length; g < l2; g++) {
      let geometry = geometries[g], skip = !forceUpdate && !forceUpdateAttr && skipAssigningAttributes[g], attr = geometry.getAttribute(key);
      if (!skip)
        if (key === "color" && targetAttribute.itemSize !== attr.itemSize)
          for (let index = offset, l3 = attr.count; index < l3; index++)
            attr.setXYZW(index, targetAttribute.getX(index), targetAttribute.getY(index), targetAttribute.getZ(index), 1);
        else
          copyAttributeContents(attr, targetAttribute, offset);
      offset += attr.count;
    }
  }
}

// npm/three-gpu-pathtracer/src/core/utils/GeometryPreparationUtils.js
import { BufferAttribute as BufferAttribute6 } from "../three/build/three.module.js";
function updateMaterialIndexAttribute(geometry, materials, allMaterials) {
  let indexAttr = geometry.index, vertCount = geometry.attributes.position.count, totalCount = indexAttr ? indexAttr.count : vertCount, groups = geometry.groups;
  groups.length === 0 && (groups = [{ count: totalCount, start: 0, materialIndex: 0 }]);
  let materialIndexAttribute = geometry.getAttribute("materialIndex");
  if (!materialIndexAttribute || materialIndexAttribute.count !== vertCount) {
    let array;
    allMaterials.length <= 255 ? array = new Uint8Array(vertCount) : array = new Uint16Array(vertCount), materialIndexAttribute = new BufferAttribute6(array, 1, !1), geometry.deleteAttribute("materialIndex"), geometry.setAttribute("materialIndex", materialIndexAttribute);
  }
  let materialArray = materialIndexAttribute.array;
  for (let i = 0; i < groups.length; i++) {
    let group = groups[i], start = group.start, count = group.count, endCount = Math.min(count, totalCount - start), mat = Array.isArray(materials) ? materials[group.materialIndex] : materials, materialIndex = allMaterials.indexOf(mat);
    for (let j = 0; j < endCount; j++) {
      let index = start + j;
      indexAttr && (index = indexAttr.getX(index)), materialArray[index] = materialIndex;
    }
  }
}
function setCommonAttributes(geometry, attributes, meshMaterial) {
  if (!geometry.index) {
    let indexCount = geometry.attributes.position.count, array = new Array(indexCount);
    for (let i = 0; i < indexCount; i++)
      array[i] = i;
    geometry.setIndex(array);
  }
  if (!geometry.attributes.normal && attributes && attributes.includes("normal") && geometry.computeVertexNormals(), !geometry.attributes.uv && attributes && attributes.includes("uv")) {
    let vertCount = geometry.attributes.position.count;
    geometry.setAttribute("uv", new BufferAttribute6(new Float32Array(vertCount * 2), 2, !1));
  }
  for (const key of ["uv1", "uv2", "uv3"]) {
    if (attributes?.includes(key) && !geometry.hasAttribute(key))
      geometry.setAttribute(key, new BufferAttribute6(new Float32Array(geometry.attributes.position.count * 2), 2));
  }
  if (!geometry.attributes.tangent && attributes && attributes.includes("tangent"))
    if (geometry.attributes.uv && geometry.attributes.normal) {
      // glTF primitives can select a different coordinate set for the normal
      // map. When the asset supplies no tangents, derive them on that set.
      const mat = Array.isArray(meshMaterial) ? meshMaterial[0] : meshMaterial;
      const channel = (mat?.normalMap || mat?.clearcoatNormalMap)?.channel || 0;
      const originalUv = geometry.attributes.uv;
      if (channel && geometry.hasAttribute("uv" + channel))
        geometry.setAttribute("uv", geometry.getAttribute("uv" + channel));
      geometry.computeTangents();
      geometry.setAttribute("uv", originalUv);
    } else {
      let vertCount = geometry.attributes.position.count;
      geometry.setAttribute("tangent", new BufferAttribute6(new Float32Array(vertCount * 4), 4, !1));
    }
  if (!geometry.attributes.color && attributes && attributes.includes("color")) {
    let vertCount = geometry.attributes.position.count, array = new Float32Array(vertCount * 4);
    array.fill(1), geometry.setAttribute("color", new BufferAttribute6(array, 4));
  }
}

// npm/three-gpu-pathtracer/src/core/utils/BakedGeometry.js
import { BufferGeometry as BufferGeometry3 } from "../three/build/three.module.js";

// npm/three-gpu-pathtracer/src/core/utils/MeshDiff.js
import { Matrix4 as Matrix48 } from "../three/build/three.module.js";

// npm/three-gpu-pathtracer/src/utils/bufferToHash.js
function bufferToHash(buffer) {
  let hash = 0;
  if (buffer.byteLength !== 0) {
    let uintArray = new Uint8Array(buffer);
    for (let i = 0; i < buffer.byteLength; i++) {
      let byte = uintArray[i];
      hash = (hash << 5) - hash + byte, hash |= 0;
    }
  }
  return hash;
}

// npm/three-gpu-pathtracer/src/core/utils/MeshDiff.js
function getGeometryHash(geometry) {
  let hash = geometry.uuid, attributes = Object.values(geometry.attributes);
  geometry.index && (attributes.push(geometry.index), hash += `index|${geometry.index.version}`);
  let keys = Object.keys(attributes).sort();
  for (let key of keys) {
    let attr = attributes[key];
    hash += `${key}_${attr.version}|`;
  }
  return hash;
}
function getSkeletonHash(mesh) {
  let skeleton = mesh.skeleton;
  return skeleton ? (skeleton.boneTexture || skeleton.computeBoneTexture(), `${bufferToHash(skeleton.boneTexture.image.data.buffer)}_${skeleton.boneTexture.uuid}`) : null;
}
var MeshDiff = class {
  constructor(mesh = null) {
    this.matrixWorld = new Matrix48(), this.geometryHash = null, this.skeletonHash = null, this.primitiveCount = -1, mesh !== null && this.updateFrom(mesh);
  }
  updateFrom(mesh) {
    let geometry = mesh.geometry, primitiveCount = (geometry.index ? geometry.index.count : geometry.attributes.position.count) / 3;
    this.matrixWorld.copy(mesh.matrixWorld), this.geometryHash = getGeometryHash(geometry), this.primitiveCount = primitiveCount, this.skeletonHash = getSkeletonHash(mesh);
  }
  didChange(mesh) {
    let geometry = mesh.geometry, primitiveCount = (geometry.index ? geometry.index.count : geometry.attributes.position.count) / 3;
    return !(this.matrixWorld.equals(mesh.matrixWorld) && this.geometryHash === getGeometryHash(geometry) && this.skeletonHash === getSkeletonHash(mesh) && this.primitiveCount === primitiveCount);
  }
};

// npm/three-gpu-pathtracer/src/core/utils/convertToStaticGeometry.js
import { BufferGeometry as BufferGeometry2, Matrix3, Matrix4 as Matrix49, Vector3 as Vector311, Vector4 } from "../three/build/three.module.js";
var _positionVector = /* @__PURE__ */ new Vector311(), _normalVector = /* @__PURE__ */ new Vector311(), _tangentVector = /* @__PURE__ */ new Vector311(), _tangentVector4 = /* @__PURE__ */ new Vector4(), _morphVector = /* @__PURE__ */ new Vector311(), _temp = /* @__PURE__ */ new Vector311(), _skinIndex = /* @__PURE__ */ new Vector4(), _skinWeight = /* @__PURE__ */ new Vector4(), _matrix = /* @__PURE__ */ new Matrix49(), _boneMatrix = /* @__PURE__ */ new Matrix49();
function boneNormalTransform(mesh, index, target2) {
  let skeleton = mesh.skeleton, geometry = mesh.geometry, bones = skeleton.bones, boneInverses = skeleton.boneInverses;
  _skinIndex.fromBufferAttribute(geometry.attributes.skinIndex, index), _skinWeight.fromBufferAttribute(geometry.attributes.skinWeight, index), _matrix.elements.fill(0);
  for (let i = 0; i < 4; i++) {
    let weight = _skinWeight.getComponent(i);
    if (weight !== 0) {
      let boneIndex = _skinIndex.getComponent(i);
      _boneMatrix.multiplyMatrices(bones[boneIndex].matrixWorld, boneInverses[boneIndex]), addScaledMatrix(_matrix, _boneMatrix, weight);
    }
  }
  return _matrix.multiply(mesh.bindMatrix).premultiply(mesh.bindMatrixInverse), target2.transformDirection(_matrix), target2;
}
function applyMorphTarget(morphData, morphInfluences, morphTargetsRelative, i, target2) {
  _morphVector.set(0, 0, 0);
  for (let j = 0, jl = morphData.length; j < jl; j++) {
    let influence = morphInfluences[j], morphAttribute = morphData[j];
    influence !== 0 && (_temp.fromBufferAttribute(morphAttribute, i), morphTargetsRelative ? _morphVector.addScaledVector(_temp, influence) : _morphVector.addScaledVector(_temp.sub(target2), influence));
  }
  target2.add(_morphVector);
}
function addScaledMatrix(target2, matrix, scale) {
  let targetArray = target2.elements, matrixArray = matrix.elements;
  for (let i = 0, l = matrixArray.length; i < l; i++)
    targetArray[i] += matrixArray[i] * scale;
}
function invertGeometry(geometry) {
  let { index, attributes } = geometry;
  if (index)
    for (let i = 0, l = index.count; i < l; i += 3) {
      let v0 = index.getX(i), v2 = index.getX(i + 2);
      index.setX(i, v2), index.setX(i + 2, v0);
    }
  else
    for (let key in attributes) {
      let attr = attributes[key], itemSize = attr.itemSize;
      for (let i = 0, l = attr.count; i < l; i += 3)
        for (let j = 0; j < itemSize; j++) {
          let v0 = attr.getComponent(i, j), v2 = attr.getComponent(i + 2, j);
          attr.setComponent(i, j, v2), attr.setComponent(i + 2, j, v0);
        }
    }
  return geometry;
}
function convertToStaticGeometry(mesh, options = {}, targetGeometry = new BufferGeometry2()) {
  options = {
    applyWorldTransforms: !0,
    attributes: [],
    ...options
  };
  let geometry = mesh.geometry, applyWorldTransforms = options.applyWorldTransforms, includeNormal = options.attributes.includes("normal"), includeTangent = options.attributes.includes("tangent"), attributes = geometry.attributes, targetAttributes = targetGeometry.attributes;
  for (let key in targetGeometry.attributes)
    (!options.attributes.includes(key) || !(key in geometry.attributes)) && targetGeometry.deleteAttribute(key);
  !targetGeometry.index && geometry.index && (targetGeometry.index = geometry.index.clone()), targetAttributes.position || targetGeometry.setAttribute("position", createAttributeClone(attributes.position)), includeNormal && !targetAttributes.normal && attributes.normal && targetGeometry.setAttribute("normal", createAttributeClone(attributes.normal)), includeTangent && !targetAttributes.tangent && attributes.tangent && targetGeometry.setAttribute("tangent", createAttributeClone(attributes.tangent)), validateAttributes(geometry.index, targetGeometry.index), validateAttributes(attributes.position, targetAttributes.position), includeNormal && validateAttributes(attributes.normal, targetAttributes.normal), includeTangent && validateAttributes(attributes.tangent, targetAttributes.tangent);
  let position = attributes.position, normal = includeNormal ? attributes.normal : null, tangent = includeTangent ? attributes.tangent : null, morphPosition = geometry.morphAttributes.position, morphNormal = geometry.morphAttributes.normal, morphTangent = geometry.morphAttributes.tangent, morphTargetsRelative = geometry.morphTargetsRelative, morphInfluences = mesh.morphTargetInfluences, normalMatrix = new Matrix3();
  normalMatrix.getNormalMatrix(mesh.matrixWorld), geometry.index && targetGeometry.index.array.set(geometry.index.array);
  for (let i = 0, l = attributes.position.count; i < l; i++)
    _positionVector.fromBufferAttribute(position, i), normal && _normalVector.fromBufferAttribute(normal, i), tangent && (_tangentVector4.fromBufferAttribute(tangent, i), _tangentVector.fromBufferAttribute(tangent, i)), morphInfluences && (morphPosition && applyMorphTarget(morphPosition, morphInfluences, morphTargetsRelative, i, _positionVector), morphNormal && applyMorphTarget(morphNormal, morphInfluences, morphTargetsRelative, i, _normalVector), morphTangent && applyMorphTarget(morphTangent, morphInfluences, morphTargetsRelative, i, _tangentVector)), mesh.isSkinnedMesh && (mesh.applyBoneTransform(i, _positionVector), normal && boneNormalTransform(mesh, i, _normalVector), tangent && boneNormalTransform(mesh, i, _tangentVector)), applyWorldTransforms && _positionVector.applyMatrix4(mesh.matrixWorld), targetAttributes.position.setXYZ(i, _positionVector.x, _positionVector.y, _positionVector.z), normal && (applyWorldTransforms && _normalVector.applyNormalMatrix(normalMatrix), targetAttributes.normal.setXYZ(i, _normalVector.x, _normalVector.y, _normalVector.z)), tangent && (applyWorldTransforms && _tangentVector.transformDirection(mesh.matrixWorld), targetAttributes.tangent.setXYZW(i, _tangentVector.x, _tangentVector.y, _tangentVector.z, _tangentVector4.w));
  for (let i in options.attributes) {
    let key = options.attributes[i];
    key === "position" || key === "tangent" || key === "normal" || !(key in attributes) || (targetAttributes[key] || targetGeometry.setAttribute(key, createAttributeClone(attributes[key])), validateAttributes(attributes[key], targetAttributes[key]), copyAttributeContents(attributes[key], targetAttributes[key]));
  }
  return mesh.matrixWorld.determinant() < 0 && invertGeometry(targetGeometry), targetGeometry;
}

// npm/three-gpu-pathtracer/src/core/utils/BakedGeometry.js
var BakedGeometry = class extends BufferGeometry3 {
  constructor() {
    super(), this.version = 0, this.hash = null, this._diff = new MeshDiff();
  }
  // returns whether the passed mesh is compatible with this baked geometry
  // such that it can be updated without resizing attributes
  isCompatible(mesh, attributes) {
    let geometry = mesh.geometry;
    for (let i = 0; i < attributes.length; i++) {
      let key = attributes[i], attr1 = geometry.attributes[key], attr2 = this.attributes[key];
      if (attr1 && !validateAttributes(attr1, attr2))
        return !1;
    }
    return !0;
  }
  updateFrom(mesh, options) {
    let diff = this._diff;
    return diff.didChange(mesh) ? (convertToStaticGeometry(mesh, options, this), diff.updateFrom(mesh), this.version++, this.hash = `${this.uuid}_${this.version}`, !0) : !1;
  }
};

// npm/three-gpu-pathtracer/src/core/utils/StaticGeometryGenerator.js
var NO_CHANGE = 0, GEOMETRY_ADJUSTED = 1, GEOMETRY_REBUILT = 2;
function flatTraverseMeshes(objects, cb) {
  for (let i = 0, l = objects.length; i < l; i++)
    objects[i].traverseVisible((o) => {
      o.isMesh && cb(o);
    });
}
function getMaterials(meshes) {
  let materials = [];
  for (let i = 0, l = meshes.length; i < l; i++) {
    let mesh = meshes[i];
    Array.isArray(mesh.material) ? materials.push(...mesh.material) : materials.push(mesh.material);
  }
  return materials;
}
function mergeGeometryList(geometries, target2, options) {
  if (geometries.length === 0) {
    target2.setIndex(null);
    let attrs = target2.attributes;
    for (let key in attrs)
      target2.deleteAttribute(key);
    for (let key in options.attributes)
      target2.setAttribute(options.attributes[key], new BufferAttribute7(new Float32Array(0), 4, !1));
  } else
    mergeGeometries(geometries, options, target2);
  for (let key in target2.attributes)
    target2.attributes[key].needsUpdate = !0;
}
var StaticGeometryGenerator = class {
  constructor(objects) {
    this.objects = null, this.useGroups = !0, this.applyWorldTransforms = !0, this.generateMissingAttributes = !0, this.overwriteIndex = !0, this.attributes = ["position", "normal", "color", "tangent", "uv", "uv2"], this._intermediateGeometry = /* @__PURE__ */ new Map(), this._geometryMergeSets = /* @__PURE__ */ new WeakMap(), this._mergeOrder = [], this._dummyMesh = null, this.setObjects(objects || []);
  }
  _getDummyMesh() {
    if (!this._dummyMesh) {
      let dummyMaterial = new MeshBasicMaterial(), emptyGeometry = new BufferGeometry4();
      emptyGeometry.setAttribute("position", new BufferAttribute7(new Float32Array(9), 3)), this._dummyMesh = new Mesh(emptyGeometry, dummyMaterial);
    }
    return this._dummyMesh;
  }
  _getMeshes() {
    let meshes = [];
    return flatTraverseMeshes(this.objects, (mesh) => {
      meshes.push(mesh);
    }), meshes.sort((a, b) => a.uuid > b.uuid ? 1 : a.uuid < b.uuid ? -1 : 0), meshes.length === 0 && meshes.push(this._getDummyMesh()), meshes;
  }
  _updateIntermediateGeometries() {
    let { _intermediateGeometry } = this, meshes = this._getMeshes(), unusedMeshKeys = new Set(_intermediateGeometry.keys()), convertOptions = {
      attributes: this.attributes,
      applyWorldTransforms: this.applyWorldTransforms
    };
    for (let i = 0, l = meshes.length; i < l; i++) {
      let mesh = meshes[i], meshKey = mesh.uuid;
      unusedMeshKeys.delete(meshKey);
      let geom = _intermediateGeometry.get(meshKey);
      (!geom || !geom.isCompatible(mesh, this.attributes)) && (geom && geom.dispose(), geom = new BakedGeometry(), _intermediateGeometry.set(meshKey, geom)), geom.updateFrom(mesh, convertOptions) && this.generateMissingAttributes && setCommonAttributes(geom, this.attributes, mesh.material);
    }
    unusedMeshKeys.forEach((key) => {
      _intermediateGeometry.delete(key);
    });
  }
  setObjects(objects) {
    Array.isArray(objects) ? this.objects = [...objects] : this.objects = [objects];
  }
  generate(targetGeometry = new BufferGeometry4()) {
    let { useGroups, overwriteIndex, _intermediateGeometry, _geometryMergeSets } = this, meshes = this._getMeshes(), skipAssigningAttributes = [], mergeGeometry = [], previousMergeInfo = _geometryMergeSets.get(targetGeometry) || [];
    this._updateIntermediateGeometries();
    let forceUpdate = !1;
    meshes.length !== previousMergeInfo.length && (forceUpdate = !0);
    for (let i = 0, l = meshes.length; i < l; i++) {
      let mesh = meshes[i], geom = _intermediateGeometry.get(mesh.uuid);
      mergeGeometry.push(geom);
      let info = previousMergeInfo[i];
      !info || info.uuid !== geom.uuid ? (skipAssigningAttributes.push(!1), forceUpdate = !0) : info.version !== geom.version ? skipAssigningAttributes.push(!1) : skipAssigningAttributes.push(!0);
    }
    mergeGeometryList(mergeGeometry, targetGeometry, { useGroups, forceUpdate, skipAssigningAttributes, overwriteIndex }), forceUpdate && targetGeometry.dispose(), _geometryMergeSets.set(targetGeometry, mergeGeometry.map((g) => ({
      version: g.version,
      uuid: g.uuid
    })));
    let changeType = NO_CHANGE;
    return forceUpdate ? changeType = GEOMETRY_REBUILT : skipAssigningAttributes.includes(!1) && (changeType = GEOMETRY_ADJUSTED), {
      changeType,
      materials: getMaterials(meshes),
      geometry: targetGeometry
    };
  }
};

// npm/three-gpu-pathtracer/src/core/PathTracingSceneGenerator.js
function getTextures(materials) {
  let textureSet = /* @__PURE__ */ new Set();
  for (let i = 0, l = materials.length; i < l; i++) {
    let material = materials[i];
    for (let key in material) {
      let value = material[key];
      value && value.isTexture && textureSet.add(value);
    }
  }
  return Array.from(textureSet);
}
function getLights(objects) {
  let lights = [], iesSet = /* @__PURE__ */ new Set();
  for (let i = 0, l = objects.length; i < l; i++)
    objects[i].traverse((c) => {
      c.visible && (c.isRectAreaLight || c.isSpotLight || c.isPointLight || c.isDirectionalLight) && (lights.push(c), c.iesMap && iesSet.add(c.iesMap));
    });
  let iesTextures = Array.from(iesSet).sort((a, b) => a.uuid < b.uuid ? 1 : a.uuid > b.uuid ? -1 : 0);
  return { lights, iesTextures };
}
var PathTracingSceneGenerator = class {
  get initialized() {
    return !!this.bvh;
  }
  constructor(objects) {
    this.bvhOptions = {}, this.attributes = ["position", "normal", "tangent", "color", "uv", "uv2"], this.generateBVH = !0, this.bvh = null, this.geometry = new BufferGeometry5(), this.staticGeometryGenerator = new StaticGeometryGenerator(objects), this._bvhWorker = null, this._pendingGenerate = null, this._buildAsync = !1, this._materialUuids = null;
  }
  setObjects(objects) {
    this.staticGeometryGenerator.setObjects(objects);
  }
  setBVHWorker(bvhWorker) {
    this._bvhWorker = bvhWorker;
  }
  async generateAsync(onProgress = null) {
    if (!this._bvhWorker)
      throw new Error('PathTracingSceneGenerator: "setBVHWorker" must be called before "generateAsync" can be called.');
    if (this.bvh instanceof Promise)
      return this._pendingGenerate || (this._pendingGenerate = new Promise(async () => (await this.bvh, this._pendingGenerate = null, this.generateAsync(onProgress)))), this._pendingGenerate;
    {
      this._buildAsync = !0;
      let result = this.generate(onProgress);
      return this._buildAsync = !1, result.bvh = this.bvh = await result.bvh, result;
    }
  }
  generate(onProgress = null) {
    let { staticGeometryGenerator, geometry, attributes } = this, objects = staticGeometryGenerator.objects;
    staticGeometryGenerator.attributes = attributes, objects.forEach((o) => {
      o.traverse((c) => {
        c.isSkinnedMesh && c.skeleton && c.skeleton.update();
      });
    });
    let result = staticGeometryGenerator.generate(geometry), materials = result.materials, needsMaterialIndexUpdate = result.changeType !== NO_CHANGE || this._materialUuids === null || this._materialUuids.length !== materials.length;
    if (!needsMaterialIndexUpdate) {
      for (let i = 0, length = materials.length; i < length; i++)
        if (materials[i].uuid !== this._materialUuids[i]) {
          needsMaterialIndexUpdate = !0;
          break;
        }
    }
    let textures = getTextures(materials), { lights, iesTextures } = getLights(objects);
    if (needsMaterialIndexUpdate && (updateMaterialIndexAttribute(geometry, materials, materials), this._materialUuids = materials.map((material) => material.uuid)), this.generateBVH) {
      if (this.bvh instanceof Promise)
        throw new Error("PathTracingSceneGenerator: BVH is already building asynchronously.");
      if (result.changeType === GEOMETRY_REBUILT) {
        let bvhOptions = {
          strategy: 2,
          maxLeafTris: 1,
          indirect: !0,
          onProgress,
          ...this.bvhOptions
        };
        this._buildAsync ? this.bvh = this._bvhWorker.generate(geometry, bvhOptions) : this.bvh = new MeshBVH(geometry, bvhOptions);
      } else result.changeType === GEOMETRY_ADJUSTED && this.bvh.refit();
    }
    return {
      bvhChanged: result.changeType !== NO_CHANGE,
      bvh: this.bvh,
      needsMaterialIndexUpdate,
      lights,
      iesTextures,
      geometry,
      materials,
      textures,
      objects
    };
  }
};

// npm/three-gpu-pathtracer/src/core/PathTracingRenderer.js
import { RGBAFormat as RGBAFormat11, FloatType as FloatType10, Color as Color2, Vector2 as Vector27, WebGLRenderTarget as WebGLRenderTarget2, NoBlending as NoBlending3, NormalBlending, Vector4 as Vector42, NearestFilter as NearestFilter8 } from "../three/build/three.module.js";

// npm/three/examples/jsm/postprocessing/Pass.js
import {
  BufferGeometry as BufferGeometry6,
  Float32BufferAttribute,
  OrthographicCamera,
  Mesh as Mesh2
} from "../three/build/three.module.js";
var _camera = new OrthographicCamera(-1, 1, 1, -1, 0, 1), FullscreenTriangleGeometry = class extends BufferGeometry6 {
  constructor() {
    super(), this.setAttribute("position", new Float32BufferAttribute([-1, 3, 0, -1, -1, 0, 3, -1, 0], 3)), this.setAttribute("uv", new Float32BufferAttribute([0, 2, 0, 0, 2, 0], 2));
  }
}, _geometry = new FullscreenTriangleGeometry(), FullScreenQuad = class {
  /**
   * Constructs a new full screen quad.
   *
   * @param {?Material} material - The material to render te full screen quad with.
   */
  constructor(material) {
    this._mesh = new Mesh2(_geometry, material);
  }
  /**
   * Frees the GPU-related resources allocated by this instance. Call this
   * method whenever the instance is no longer used in your app.
   */
  dispose() {
    this._mesh.geometry.dispose();
  }
  /**
   * Renders the full screen quad.
   *
   * @param {WebGLRenderer} renderer - The renderer.
   */
  render(renderer) {
    renderer.render(this._mesh, _camera);
  }
  /**
   * The quad's material.
   *
   * @type {?Material}
   */
  get material() {
    return this._mesh.material;
  }
  set material(value) {
    this._mesh.material = value;
  }
};

// npm/three-gpu-pathtracer/src/materials/fullscreen/BlendMaterial.js
import { NoBlending } from "../three/build/three.module.js";

// npm/three-gpu-pathtracer/src/materials/MaterialBase.js
import { ShaderMaterial } from "../three/build/three.module.js";
var MaterialBase = class extends ShaderMaterial {
  set needsUpdate(v2) {
    super.needsUpdate = !0, this.dispatchEvent({
      type: "recompilation"
    });
  }
  constructor(shader) {
    super(shader);
    for (let key in this.uniforms)
      Object.defineProperty(this, key, {
        get() {
          return this.uniforms[key].value;
        },
        set(v2) {
          this.uniforms[key].value = v2;
        }
      });
  }
  // sets the given named define value and sets "needsUpdate" to true if it's different
  setDefine(name, value = void 0) {
    if (value == null) {
      if (name in this.defines)
        return delete this.defines[name], this.needsUpdate = !0, !0;
    } else if (this.defines[name] !== value)
      return this.defines[name] = value, this.needsUpdate = !0, !0;
    return !1;
  }
};

// npm/three-gpu-pathtracer/src/materials/fullscreen/BlendMaterial.js
var BlendMaterial = class extends MaterialBase {
  constructor(parameters) {
    super({
      blending: NoBlending,
      uniforms: {
        target1: { value: null },
        target2: { value: null },
        opacity: { value: 1 }
      },
      vertexShader: (
        /* glsl */
        `

				varying vec2 vUv;

				void main() {

					vUv = uv;
					gl_Position = projectionMatrix * modelViewMatrix * vec4( position, 1.0 );

				}`
      ),
      fragmentShader: (
        /* glsl */
        `

				uniform float opacity;

				uniform sampler2D target1;
				uniform sampler2D target2;

				varying vec2 vUv;

				void main() {

					vec4 color1 = texture2D( target1, vUv );
					vec4 color2 = texture2D( target2, vUv );

					float invOpacity = 1.0 - opacity;
					float totalAlpha = color1.a * invOpacity + color2.a * opacity;

					if ( color1.a != 0.0 || color2.a != 0.0 ) {

						gl_FragColor.rgb = color1.rgb * ( invOpacity * color1.a / totalAlpha ) + color2.rgb * ( opacity * color2.a / totalAlpha );
						gl_FragColor.a = totalAlpha;

					} else {

						gl_FragColor = vec4( 0.0 );

					}

				}`
      )
    }), this.setValues(parameters);
  }
};

// npm/three-gpu-pathtracer/src/utils/SobolNumberMapGenerator.js
import { FloatType as FloatType3, NearestFilter as NearestFilter3, NoBlending as NoBlending2, RGBAFormat as RGBAFormat3, Vector2 as Vector25, WebGLRenderTarget } from "../three/build/three.module.js";

// npm/three-gpu-pathtracer/src/shader/rand/sobol.glsl.js
function generateSobolFunctionVariants(dim = 1) {
  let type = "uint";
  return dim > 1 && (type = "uvec" + dim), /* glsl */
  `
		${type} sobolReverseBits( ${type} x ) {

			x = ( ( ( x & 0xaaaaaaaau ) >> 1 ) | ( ( x & 0x55555555u ) << 1 ) );
			x = ( ( ( x & 0xccccccccu ) >> 2 ) | ( ( x & 0x33333333u ) << 2 ) );
			x = ( ( ( x & 0xf0f0f0f0u ) >> 4 ) | ( ( x & 0x0f0f0f0fu ) << 4 ) );
			x = ( ( ( x & 0xff00ff00u ) >> 8 ) | ( ( x & 0x00ff00ffu ) << 8 ) );
			return ( ( x >> 16 ) | ( x << 16 ) );

		}

		${type} sobolHashCombine( uint seed, ${type} v ) {

			return seed ^ ( v + ${type}( ( seed << 6 ) + ( seed >> 2 ) ) );

		}

		${type} sobolLaineKarrasPermutation( ${type} x, ${type} seed ) {

			x += seed;
			x ^= x * 0x6c50b47cu;
			x ^= x * 0xb82f1e52u;
			x ^= x * 0xc7afe638u;
			x ^= x * 0x8d22f6e6u;
			return x;

		}

		${type} nestedUniformScrambleBase2( ${type} x, ${type} seed ) {

			x = sobolLaineKarrasPermutation( x, seed );
			x = sobolReverseBits( x );
			return x;

		}
	`;
}
function generateSobolSampleFunctions(dim = 1) {
  let utype = "uint", vtype = "float", num = "", components = ".r", combineValues = "1u";
  return dim > 1 && (utype = "uvec" + dim, vtype = "vec" + dim, num = dim + "", dim === 2 ? (components = ".rg", combineValues = "uvec2( 1u, 2u )") : dim === 3 ? (components = ".rgb", combineValues = "uvec3( 1u, 2u, 3u )") : (components = "", combineValues = "uvec4( 1u, 2u, 3u, 4u )")), /* glsl */
  `

		${vtype} sobol${num}( int effect ) {

			uint seed = sobolGetSeed( sobolBounceIndex, uint( effect ) );
			uint index = sobolPathIndex;

			uint shuffle_seed = sobolHashCombine( seed, 0u );
			uint shuffled_index = nestedUniformScrambleBase2( sobolReverseBits( index ), shuffle_seed );
			${vtype} sobol_pt = sobolGetTexturePoint( shuffled_index )${components};
			${utype} result = ${utype}( sobol_pt * 16777216.0 );

			${utype} seed2 = sobolHashCombine( seed, ${combineValues} );
			result = nestedUniformScrambleBase2( result, seed2 );

			return SOBOL_FACTOR * ${vtype}( result >> 8 );

		}
	`;
}
var sobol_common = (
  /* glsl */
  `

	// Utils
	const float SOBOL_FACTOR = 1.0 / 16777216.0;
	const uint SOBOL_MAX_POINTS = 256u * 256u;

	${generateSobolFunctionVariants(1)}
	${generateSobolFunctionVariants(2)}
	${generateSobolFunctionVariants(3)}
	${generateSobolFunctionVariants(4)}

	uint sobolHash( uint x ) {

		// finalizer from murmurhash3
		x ^= x >> 16;
		x *= 0x85ebca6bu;
		x ^= x >> 13;
		x *= 0xc2b2ae35u;
		x ^= x >> 16;
		return x;

	}

`
), sobol_point_generation = (
  /* glsl */
  `

	const uint SOBOL_DIRECTIONS_1[ 32 ] = uint[ 32 ](
		0x80000000u, 0xc0000000u, 0xa0000000u, 0xf0000000u,
		0x88000000u, 0xcc000000u, 0xaa000000u, 0xff000000u,
		0x80800000u, 0xc0c00000u, 0xa0a00000u, 0xf0f00000u,
		0x88880000u, 0xcccc0000u, 0xaaaa0000u, 0xffff0000u,
		0x80008000u, 0xc000c000u, 0xa000a000u, 0xf000f000u,
		0x88008800u, 0xcc00cc00u, 0xaa00aa00u, 0xff00ff00u,
		0x80808080u, 0xc0c0c0c0u, 0xa0a0a0a0u, 0xf0f0f0f0u,
		0x88888888u, 0xccccccccu, 0xaaaaaaaau, 0xffffffffu
	);

	const uint SOBOL_DIRECTIONS_2[ 32 ] = uint[ 32 ](
		0x80000000u, 0xc0000000u, 0x60000000u, 0x90000000u,
		0xe8000000u, 0x5c000000u, 0x8e000000u, 0xc5000000u,
		0x68800000u, 0x9cc00000u, 0xee600000u, 0x55900000u,
		0x80680000u, 0xc09c0000u, 0x60ee0000u, 0x90550000u,
		0xe8808000u, 0x5cc0c000u, 0x8e606000u, 0xc5909000u,
		0x6868e800u, 0x9c9c5c00u, 0xeeee8e00u, 0x5555c500u,
		0x8000e880u, 0xc0005cc0u, 0x60008e60u, 0x9000c590u,
		0xe8006868u, 0x5c009c9cu, 0x8e00eeeeu, 0xc5005555u
	);

	const uint SOBOL_DIRECTIONS_3[ 32 ] = uint[ 32 ](
		0x80000000u, 0xc0000000u, 0x20000000u, 0x50000000u,
		0xf8000000u, 0x74000000u, 0xa2000000u, 0x93000000u,
		0xd8800000u, 0x25400000u, 0x59e00000u, 0xe6d00000u,
		0x78080000u, 0xb40c0000u, 0x82020000u, 0xc3050000u,
		0x208f8000u, 0x51474000u, 0xfbea2000u, 0x75d93000u,
		0xa0858800u, 0x914e5400u, 0xdbe79e00u, 0x25db6d00u,
		0x58800080u, 0xe54000c0u, 0x79e00020u, 0xb6d00050u,
		0x800800f8u, 0xc00c0074u, 0x200200a2u, 0x50050093u
	);

	const uint SOBOL_DIRECTIONS_4[ 32 ] = uint[ 32 ](
		0x80000000u, 0x40000000u, 0x20000000u, 0xb0000000u,
		0xf8000000u, 0xdc000000u, 0x7a000000u, 0x9d000000u,
		0x5a800000u, 0x2fc00000u, 0xa1600000u, 0xf0b00000u,
		0xda880000u, 0x6fc40000u, 0x81620000u, 0x40bb0000u,
		0x22878000u, 0xb3c9c000u, 0xfb65a000u, 0xddb2d000u,
		0x78022800u, 0x9c0b3c00u, 0x5a0fb600u, 0x2d0ddb00u,
		0xa2878080u, 0xf3c9c040u, 0xdb65a020u, 0x6db2d0b0u,
		0x800228f8u, 0x400b3cdcu, 0x200fb67au, 0xb00ddb9du
	);

	uint getMaskedSobol( uint index, uint directions[ 32 ] ) {

		uint X = 0u;
		for ( int bit = 0; bit < 32; bit ++ ) {

			uint mask = ( index >> bit ) & 1u;
			X ^= mask * directions[ bit ];

		}
		return X;

	}

	vec4 generateSobolPoint( uint index ) {

		if ( index >= SOBOL_MAX_POINTS ) {

			return vec4( 0.0 );

		}

		// NOTE: this sobol "direction" is also available but we can't write out 5 components
		// uint x = index & 0x00ffffffu;
		uint x = sobolReverseBits( getMaskedSobol( index, SOBOL_DIRECTIONS_1 ) ) & 0x00ffffffu;
		uint y = sobolReverseBits( getMaskedSobol( index, SOBOL_DIRECTIONS_2 ) ) & 0x00ffffffu;
		uint z = sobolReverseBits( getMaskedSobol( index, SOBOL_DIRECTIONS_3 ) ) & 0x00ffffffu;
		uint w = sobolReverseBits( getMaskedSobol( index, SOBOL_DIRECTIONS_4 ) ) & 0x00ffffffu;

		return vec4( x, y, z, w ) * SOBOL_FACTOR;

	}

`
), sobol_functions = (
  /* glsl */
  `

	// Seeds
	uniform sampler2D sobolTexture;
	uint sobolPixelIndex = 0u;
	uint sobolPathIndex = 0u;
	uint sobolBounceIndex = 0u;

	uint sobolGetSeed( uint bounce, uint effect ) {

		return sobolHash(
			sobolHashCombine(
				sobolHashCombine(
					sobolHash( bounce ),
					sobolPixelIndex
				),
				effect
			)
		);

	}

	vec4 sobolGetTexturePoint( uint index ) {

		if ( index >= SOBOL_MAX_POINTS ) {

			index = index % SOBOL_MAX_POINTS;

		}

		uvec2 dim = uvec2( textureSize( sobolTexture, 0 ).xy );
		uint y = index / dim.x;
		uint x = index - y * dim.x;
		vec2 uv = vec2( x, y ) / vec2( dim );
		return texture( sobolTexture, uv );

	}

	${generateSobolSampleFunctions(1)}
	${generateSobolSampleFunctions(2)}
	${generateSobolSampleFunctions(3)}
	${generateSobolSampleFunctions(4)}

`
);

// npm/three-gpu-pathtracer/src/utils/SobolNumberMapGenerator.js
var SobolNumbersMaterial = class extends MaterialBase {
  constructor() {
    super({
      blending: NoBlending2,
      uniforms: {
        resolution: { value: new Vector25() }
      },
      vertexShader: (
        /* glsl */
        `

				varying vec2 vUv;
				void main() {

					vUv = uv;
					gl_Position = projectionMatrix * modelViewMatrix * vec4( position, 1.0 );

				}
			`
      ),
      fragmentShader: (
        /* glsl */
        `

				${sobol_common}
				${sobol_point_generation}

				varying vec2 vUv;
				uniform vec2 resolution;
				void main() {

					uint index = uint( gl_FragCoord.y ) * uint( resolution.x ) + uint( gl_FragCoord.x );
					gl_FragColor = generateSobolPoint( index );

				}
			`
      )
    });
  }
}, SobolNumberMapGenerator = class {
  generate(renderer, dimensions = 256) {
    let target2 = new WebGLRenderTarget(dimensions, dimensions, {
      type: FloatType3,
      format: RGBAFormat3,
      minFilter: NearestFilter3,
      magFilter: NearestFilter3,
      generateMipmaps: !1
    }), ogTarget = renderer.getRenderTarget();
    renderer.setRenderTarget(target2);
    let quad = new FullScreenQuad(new SobolNumbersMaterial());
    return quad.material.resolution.set(dimensions, dimensions), quad.render(renderer), renderer.setRenderTarget(ogTarget), quad.dispose(), target2;
  }
};

// npm/three-gpu-pathtracer/src/materials/pathtracing/PhysicalPathTracingMaterial.js
import { ClampToEdgeWrapping as ClampToEdgeWrapping4, HalfFloatType as HalfFloatType2, Matrix4 as Matrix411, Vector2 as Vector26 } from "../three/build/three.module.js";

// npm/three-gpu-pathtracer/src/objects/PhysicalCamera.js
import { PerspectiveCamera } from "../three/build/three.module.js";
var PhysicalCamera = class extends PerspectiveCamera {
  set bokehSize(size) {
    this.fStop = this.getFocalLength() / size;
  }
  get bokehSize() {
    return this.getFocalLength() / this.fStop;
  }
  constructor(...args) {
    super(...args), this.fStop = 1.4, this.apertureBlades = 0, this.apertureRotation = 0, this.focusDistance = 25, this.anamorphicRatio = 1;
  }
  copy(source, recursive) {
    return super.copy(source, recursive), this.fStop = source.fStop, this.apertureBlades = source.apertureBlades, this.apertureRotation = source.apertureRotation, this.focusDistance = source.focusDistance, this.anamorphicRatio = source.anamorphicRatio, this;
  }
};

// npm/three-gpu-pathtracer/src/uniforms/PhysicalCameraUniform.js
var PhysicalCameraUniform = class {
  constructor() {
    this.bokehSize = 0, this.apertureBlades = 0, this.apertureRotation = 0, this.focusDistance = 10, this.anamorphicRatio = 1;
  }
  updateFrom(camera) {
    camera instanceof PhysicalCamera ? (this.bokehSize = camera.bokehSize, this.apertureBlades = camera.apertureBlades, this.apertureRotation = camera.apertureRotation, this.focusDistance = camera.focusDistance, this.anamorphicRatio = camera.anamorphicRatio) : (this.bokehSize = 0, this.apertureRotation = 0, this.apertureBlades = 0, this.focusDistance = 10, this.anamorphicRatio = 1);
  }
};

// npm/three-gpu-pathtracer/src/uniforms/EquirectHdrInfoUniform.js
import { DataTexture as DataTexture3, RedFormat as RedFormat2, LinearFilter, DataUtils as DataUtils2, HalfFloatType, TextureSource, RepeatWrapping, RGBAFormat as RGBAFormat4, FloatType as FloatType4, ClampToEdgeWrapping } from "../three/build/three.module.js";

// npm/three-gpu-pathtracer/src/utils/TextureUtils.js
import { DataUtils } from "../three/build/three.module.js";
function toHalfFloatArray(f32Array) {
  let f16Array = new Uint16Array(f32Array.length);
  for (let i = 0, n = f32Array.length; i < n; ++i)
    f16Array[i] = DataUtils.toHalfFloat(f32Array[i]);
  return f16Array;
}

// npm/three-gpu-pathtracer/src/uniforms/EquirectHdrInfoUniform.js
function binarySearchFindClosestIndexOf(array, targetValue, offset = 0, count = array.length) {
  let lower = offset, upper = offset + count - 1;
  for (; lower < upper; ) {
    let mid = lower + upper >> 1;
    array[mid] < targetValue ? lower = mid + 1 : upper = mid;
  }
  return lower - offset;
}
function colorToLuminance(r, g, b) {
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
function preprocessEnvMap(envMap, targetType = HalfFloatType) {
  let map = envMap.clone();
  map.source = new TextureSource({ ...map.image });
  let { width, height, data } = map.image, newData = data;
  if (map.type !== targetType) {
    targetType === HalfFloatType ? newData = new Uint16Array(data.length) : newData = new Float32Array(data.length);
    let maxIntValue;
    data instanceof Int8Array || data instanceof Int16Array || data instanceof Int32Array ? maxIntValue = 2 ** (8 * data.BYTES_PER_ELEMENT - 1) - 1 : maxIntValue = 2 ** (8 * data.BYTES_PER_ELEMENT) - 1;
    for (let i = 0, l = data.length; i < l; i++) {
      let v2 = data[i];
      map.type === HalfFloatType && (v2 = DataUtils2.fromHalfFloat(data[i])), map.type !== FloatType4 && map.type !== HalfFloatType && (v2 /= maxIntValue), targetType === HalfFloatType && (newData[i] = DataUtils2.toHalfFloat(v2));
    }
    map.image.data = newData, map.type = targetType;
  }
  if (map.flipY) {
    let ogData = newData;
    newData = newData.slice();
    for (let y = 0; y < height; y++)
      for (let x = 0; x < width; x++) {
        let newY = height - y - 1, ogIndex = 4 * (y * width + x), newIndex = 4 * (newY * width + x);
        newData[newIndex + 0] = ogData[ogIndex + 0], newData[newIndex + 1] = ogData[ogIndex + 1], newData[newIndex + 2] = ogData[ogIndex + 2], newData[newIndex + 3] = ogData[ogIndex + 3];
      }
    map.flipY = !1, map.image.data = newData;
  }
  return map;
}
var EquirectHdrInfoUniform = class {
  constructor() {
    let blackTex = new DataTexture3(toHalfFloatArray(new Float32Array([0, 0, 0, 0])), 1, 1);
    blackTex.type = HalfFloatType, blackTex.format = RGBAFormat4, blackTex.minFilter = LinearFilter, blackTex.magFilter = LinearFilter, blackTex.wrapS = RepeatWrapping, blackTex.wrapT = RepeatWrapping, blackTex.generateMipmaps = !1, blackTex.needsUpdate = !0;
    let marginalWeights = new DataTexture3(toHalfFloatArray(new Float32Array([0, 1])), 1, 2);
    marginalWeights.type = HalfFloatType, marginalWeights.format = RedFormat2, marginalWeights.minFilter = LinearFilter, marginalWeights.magFilter = LinearFilter, marginalWeights.generateMipmaps = !1, marginalWeights.needsUpdate = !0;
    let conditionalWeights = new DataTexture3(toHalfFloatArray(new Float32Array([0, 0, 1, 1])), 2, 2);
    conditionalWeights.type = HalfFloatType, conditionalWeights.format = RedFormat2, conditionalWeights.minFilter = LinearFilter, conditionalWeights.magFilter = LinearFilter, conditionalWeights.generateMipmaps = !1, conditionalWeights.needsUpdate = !0, this.map = blackTex, this.marginalWeights = marginalWeights, this.conditionalWeights = conditionalWeights, this.totalSum = 0;
  }
  getPixelWeight(r, g, b) {
    return colorToLuminance(r, g, b);
  }
  dispose() {
    this.marginalWeights.dispose(), this.conditionalWeights.dispose(), this.map.dispose();
  }
  updateFrom(hdr) {
    let map = preprocessEnvMap(hdr);
    map.wrapS = RepeatWrapping, map.wrapT = ClampToEdgeWrapping;
    let { width, height, data } = map.image, pdfConditional = new Float32Array(width * height), cdfConditional = new Float32Array(width * height), pdfMarginal = new Float32Array(height), cdfMarginal = new Float32Array(height), totalSumValue = 0, cumulativeWeightMarginal = 0;
    for (let y = 0; y < height; y++) {
      let cumulativeRowWeight = 0;
      for (let x = 0; x < width; x++) {
        let i = y * width + x, r = DataUtils2.fromHalfFloat(data[4 * i + 0]), g = DataUtils2.fromHalfFloat(data[4 * i + 1]), b = DataUtils2.fromHalfFloat(data[4 * i + 2]), weight = this.getPixelWeight(r, g, b, y, height);
        cumulativeRowWeight += weight, totalSumValue += weight, pdfConditional[i] = weight, cdfConditional[i] = cumulativeRowWeight;
      }
      if (cumulativeRowWeight !== 0)
        for (let i = y * width, l = y * width + width; i < l; i++)
          pdfConditional[i] /= cumulativeRowWeight, cdfConditional[i] /= cumulativeRowWeight;
      cumulativeWeightMarginal += cumulativeRowWeight, pdfMarginal[y] = cumulativeRowWeight, cdfMarginal[y] = cumulativeWeightMarginal;
    }
    if (cumulativeWeightMarginal !== 0)
      for (let i = 0, l = pdfMarginal.length; i < l; i++)
        pdfMarginal[i] /= cumulativeWeightMarginal, cdfMarginal[i] /= cumulativeWeightMarginal;
    let marginalDataArray = new Uint16Array(height), conditionalDataArray = new Uint16Array(width * height);
    for (let i = 0; i < height; i++) {
      let dist = (i + 1) / height, row = binarySearchFindClosestIndexOf(cdfMarginal, dist);
      marginalDataArray[i] = DataUtils2.toHalfFloat((row + 0.5) / height);
    }
    for (let y = 0; y < height; y++)
      for (let x = 0; x < width; x++) {
        let i = y * width + x, dist = (x + 1) / width, col = binarySearchFindClosestIndexOf(cdfConditional, dist, y * width, width);
        conditionalDataArray[i] = DataUtils2.toHalfFloat((col + 0.5) / width);
      }
    this.dispose();
    let { marginalWeights, conditionalWeights } = this;
    marginalWeights.image = { width: height, height: 1, data: marginalDataArray }, marginalWeights.needsUpdate = !0, conditionalWeights.image = { width, height, data: conditionalDataArray }, conditionalWeights.needsUpdate = !0, this.totalSum = totalSumValue, this.map = map, this.cdfMarginal = cdfMarginal, this.cdfConditional = cdfConditional;
  }
};

// npm/three-gpu-pathtracer/src/uniforms/LightsInfoUniformStruct.js
import { DataTexture as DataTexture4, RGBAFormat as RGBAFormat5, ClampToEdgeWrapping as ClampToEdgeWrapping2, FloatType as FloatType5, Vector3 as Vector312, Quaternion, Matrix4 as Matrix410, NearestFilter as NearestFilter4 } from "../three/build/three.module.js";
var LIGHT_PIXELS = 6, VISIBLE_TO_CAMERA_OFFSET = 11, RECT_AREA_LIGHT = 0, CIRC_AREA_LIGHT = 1, SPOT_LIGHT = 2, DIR_LIGHT = 3, POINT_LIGHT = 4, u = new Vector312(), v = new Vector312(), m = new Matrix410(), worldQuaternion = new Quaternion(), eye = new Vector312(), target = new Vector312(), up = new Vector312(0, 1, 0), LightsInfoUniformStruct = class {
  constructor() {
    let tex = new DataTexture4(new Float32Array(4), 1, 1);
    tex.format = RGBAFormat5, tex.type = FloatType5, tex.wrapS = ClampToEdgeWrapping2, tex.wrapT = ClampToEdgeWrapping2, tex.generateMipmaps = !1, tex.minFilter = NearestFilter4, tex.magFilter = NearestFilter4, this.tex = tex, this.count = 0;
  }
  updateFrom(lights, iesTextures = []) {
    let tex = this.tex, pixelCount = Math.max(lights.length * LIGHT_PIXELS, 1), dimension = Math.ceil(Math.sqrt(pixelCount));
    tex.image.width !== dimension && (tex.dispose(), tex.image.data = new Float32Array(dimension * dimension * 4), tex.image.width = dimension, tex.image.height = dimension);
    let floatArray = tex.image.data;
    for (let i = 0, l = lights.length; i < l; i++) {
      let l2 = lights[i], baseIndex = i * LIGHT_PIXELS * 4, index = 0;
      for (let p = 0; p < LIGHT_PIXELS * 4; p++)
        floatArray[baseIndex + p] = 0;
      l2.getWorldPosition(v), floatArray[baseIndex + index++] = v.x, floatArray[baseIndex + index++] = v.y, floatArray[baseIndex + index++] = v.z;
      let type = RECT_AREA_LIGHT;
      if (l2.isRectAreaLight && l2.isCircular ? type = CIRC_AREA_LIGHT : l2.isSpotLight ? type = SPOT_LIGHT : l2.isDirectionalLight || l2.isSunLight ? type = DIR_LIGHT : l2.isPointLight && (type = POINT_LIGHT), floatArray[baseIndex + index++] = type, floatArray[baseIndex + index++] = l2.color.r, floatArray[baseIndex + index++] = l2.color.g, floatArray[baseIndex + index++] = l2.color.b, floatArray[baseIndex + index++] = l2.intensity, l2.getWorldQuaternion(worldQuaternion), l2.isRectAreaLight)
        u.set(l2.width, 0, 0).applyQuaternion(worldQuaternion), floatArray[baseIndex + index++] = u.x, floatArray[baseIndex + index++] = u.y, floatArray[baseIndex + index++] = u.z, index++, v.set(0, l2.height, 0).applyQuaternion(worldQuaternion), floatArray[baseIndex + index++] = v.x, floatArray[baseIndex + index++] = v.y, floatArray[baseIndex + index++] = v.z, floatArray[baseIndex + index++] = u.cross(v).length() * (l2.isCircular ? Math.PI / 4 : 1);
      else if (l2.isSpotLight) {
        let radius = l2.radius || 0;
        eye.setFromMatrixPosition(l2.matrixWorld), target.setFromMatrixPosition(l2.target.matrixWorld), m.lookAt(eye, target, up), worldQuaternion.setFromRotationMatrix(m), u.set(1, 0, 0).applyQuaternion(worldQuaternion), floatArray[baseIndex + index++] = u.x, floatArray[baseIndex + index++] = u.y, floatArray[baseIndex + index++] = u.z, index++, v.set(0, 1, 0).applyQuaternion(worldQuaternion), floatArray[baseIndex + index++] = v.x, floatArray[baseIndex + index++] = v.y, floatArray[baseIndex + index++] = v.z, floatArray[baseIndex + index++] = Math.PI * radius * radius, floatArray[baseIndex + index++] = radius, floatArray[baseIndex + index++] = l2.decay, floatArray[baseIndex + index++] = l2.distance, floatArray[baseIndex + index++] = Math.cos(l2.angle), floatArray[baseIndex + index++] = Math.cos(l2.angle * (1 - l2.penumbra)), floatArray[baseIndex + index++] = l2.iesMap ? iesTextures.indexOf(l2.iesMap) : -1;
      } else if (l2.isPointLight) {
        let worldPosition = u.setFromMatrixPosition(l2.matrixWorld);
        floatArray[baseIndex + index++] = worldPosition.x, floatArray[baseIndex + index++] = worldPosition.y, floatArray[baseIndex + index++] = worldPosition.z, index++, index += 4, index += 1, floatArray[baseIndex + index++] = l2.decay, floatArray[baseIndex + index++] = l2.distance;
      } else if (l2.isDirectionalLight) {
        let worldPosition = u.setFromMatrixPosition(l2.matrixWorld), targetPosition = v.setFromMatrixPosition(l2.target.matrixWorld);
        target.subVectors(worldPosition, targetPosition).normalize(), floatArray[baseIndex + index++] = target.x, floatArray[baseIndex + index++] = target.y, floatArray[baseIndex + index++] = target.z;
      } else l2.isSunLight && (target.setFromMatrixPosition(l2.matrixWorld).normalize(), floatArray[baseIndex + index++] = target.x, floatArray[baseIndex + index++] = target.y, floatArray[baseIndex + index++] = target.z);
      floatArray[baseIndex + VISIBLE_TO_CAMERA_OFFSET] = l2.visibleToCameraRays ? 1 : 0;
    }
    this.count = lights.length;
    let hash = bufferToHash(floatArray.buffer);
    return this.hash !== hash ? (this.hash = hash, tex.needsUpdate = !0, !0) : !1;
  }
};

// npm/three-gpu-pathtracer/src/uniforms/FloatAttributeTextureArray.js
import { DataArrayTexture, FloatType as FloatType6, RGBAFormat as RGBAFormat6 } from "../three/build/three.module.js";
function copyArrayToArray(fromArray, fromStride, toArray, toStride, offset) {
  if (fromStride > toStride)
    throw new Error();
  let count = fromArray.length / fromStride, bpe = fromArray.constructor.BYTES_PER_ELEMENT * 8, maxValue = 1;
  switch (fromArray.constructor) {
    case Uint8Array:
    case Uint16Array:
    case Uint32Array:
      maxValue = 2 ** bpe - 1;
      break;
    case Int8Array:
    case Int16Array:
    case Int32Array:
      maxValue = 2 ** (bpe - 1) - 1;
      break;
  }
  for (let i = 0; i < count; i++) {
    let i4 = 4 * i, is = fromStride * i;
    for (let j = 0; j < toStride; j++)
      toArray[offset + i4 + j] = fromStride >= j + 1 ? fromArray[is + j] / maxValue : 0;
  }
}
var FloatAttributeTextureArray = class extends DataArrayTexture {
  constructor() {
    super(), this._textures = [], this.type = FloatType6, this.format = RGBAFormat6, this.internalFormat = "RGBA32F";
  }
  updateAttribute(index, attr) {
    let tex = this._textures[index];
    tex.updateFrom(attr);
    let baseImage = tex.image, image = this.image;
    if (baseImage.width !== image.width || baseImage.height !== image.height)
      throw new Error("FloatAttributeTextureArray: Attribute must be the same dimensions when updating single layer.");
    let { width, height, data } = image, offset = width * height * 4 * index, itemSize = attr.itemSize;
    itemSize === 3 && (itemSize = 4), copyArrayToArray(tex.image.data, itemSize, data, 4, offset), this.dispose(), this.needsUpdate = !0;
  }
  setAttributes(attrs) {
    let itemCount = attrs[0].count, attrsLength = attrs.length;
    for (let i = 0, l = attrsLength; i < l; i++)
      if (attrs[i].count !== itemCount)
        throw new Error("FloatAttributeTextureArray: All attributes must have the same item count.");
    let textures = this._textures;
    for (; textures.length < attrsLength; ) {
      let tex = new FloatVertexAttributeTexture();
      textures.push(tex);
    }
    for (; textures.length > attrsLength; )
      textures.pop();
    for (let i = 0, l = attrsLength; i < l; i++)
      textures[i].updateFrom(attrs[i]);
    let baseImage = textures[0].image, image = this.image;
    (baseImage.width !== image.width || baseImage.height !== image.height || baseImage.depth !== attrsLength) && (image.width = baseImage.width, image.height = baseImage.height, image.depth = attrsLength, image.data = new Float32Array(image.width * image.height * image.depth * 4));
    let { data, width, height } = image;
    for (let i = 0, l = attrsLength; i < l; i++) {
      let tex = textures[i], offset = width * height * 4 * i, itemSize = attrs[i].itemSize;
      itemSize === 3 && (itemSize = 4), copyArrayToArray(tex.image.data, itemSize, data, 4, offset);
    }
    this.dispose(), this.needsUpdate = !0;
  }
};

// npm/three-gpu-pathtracer/src/uniforms/AttributesTextureArray.js
var AttributesTextureArray = class extends FloatAttributeTextureArray {
  updateNormalAttribute(attr) {
    this.updateAttribute(0, attr);
  }
  updateTangentAttribute(attr) {
    this.updateAttribute(1, attr);
  }
  updateUvAttribute(attr) {
    this.updateAttribute(2, attr);
  }
  updateColorAttribute(attr) {
    this.updateAttribute(3, attr);
  }
  updateFrom(normal, tangent, uv, color, extraUvs) {
    this.setAttributes([normal, tangent, uv, color, ...extraUvs]);
  }
};

// npm/three-gpu-pathtracer/src/uniforms/MaterialsTexture.js
import { DataTexture as DataTexture5, RGBAFormat as RGBAFormat7, ClampToEdgeWrapping as ClampToEdgeWrapping3, FloatType as FloatType7, FrontSide as FrontSide2, BackSide as BackSide2, DoubleSide as DoubleSide2, NearestFilter as NearestFilter5 } from "../three/build/three.module.js";

// npm/three-gpu-pathtracer/src/core/utils/sceneUpdateUtils.js
function uuidSort(a, b) {
  return a.uuid < b.uuid ? 1 : a.uuid > b.uuid ? -1 : 0;
}
function getTextureHash(t) {
  return `${t.source.uuid}:${t.colorSpace}`;
}
function reduceTexturesToUniqueSources(textures) {
  let sourceSet = /* @__PURE__ */ new Set(), result = [];
  for (let i = 0, l = textures.length; i < l; i++) {
    let tex = textures[i], hash = getTextureHash(tex);
    sourceSet.has(hash) || (sourceSet.add(hash), result.push(tex));
  }
  return result;
}
function getIesTextures(lights) {
  let textures = lights.map((l) => l.iesMap || null).filter((t) => t), textureSet = new Set(textures);
  return Array.from(textureSet).sort(uuidSort);
}
function getTextures2(materials) {
  let textureSet = /* @__PURE__ */ new Set();
  for (let i = 0, l = materials.length; i < l; i++) {
    let material = materials[i];
    for (let key in material) {
      let value = material[key];
      value && value.isTexture && textureSet.add(value);
    }
  }
  let textureArray = Array.from(textureSet);
  return reduceTexturesToUniqueSources(textureArray).sort(uuidSort);
}
function getLights2(scene) {
  let lights = [];
  return scene.traverseVisible((c) => {
    (c.isRectAreaLight || c.isSpotLight || c.isPointLight || c.isDirectionalLight || c.isSunLight) && lights.push(c);
  }), lights.sort(uuidSort);
}

// npm/three-gpu-pathtracer/src/uniforms/MaterialsTexture.js
var MATERIAL_PIXELS = 53, MATERIAL_STRIDE = MATERIAL_PIXELS * 4, MaterialFeatures = class {
  constructor() {
    this._features = {};
  }
  isUsed(feature) {
    return feature in this._features;
  }
  setUsed(feature, used = !0) {
    used === !1 ? delete this._features[feature] : this._features[feature] = !0;
  }
  reset() {
    this._features = {};
  }
}, MaterialsTexture = class extends DataTexture5 {
  constructor() {
    super(new Float32Array(4), 1, 1), this.format = RGBAFormat7, this.type = FloatType7, this.wrapS = ClampToEdgeWrapping3, this.wrapT = ClampToEdgeWrapping3, this.minFilter = NearestFilter5, this.magFilter = NearestFilter5, this.generateMipmaps = !1, this.features = new MaterialFeatures();
  }
  updateFrom(materials, textures) {
    function getTexture(material, key, def = -1) {
      if (key in material && material[key]) {
        let hash2 = getTextureHash(material[key]);
        return textureLookUp[hash2];
      } else
        return def;
    }
    function getField(material, key, def) {
      return key in material ? material[key] : def;
    }
    function writeTextureMatrixToArray(material, textureKey, array, offset) {
      let texture = material[textureKey] && material[textureKey].isTexture ? material[textureKey] : null;
      if (texture) {
        texture.matrixAutoUpdate && texture.updateMatrix();
        let elements = texture.matrix.elements, i = 0;
        array[offset + i++] = elements[0], array[offset + i++] = elements[3], array[offset + i++] = elements[6], array[offset + i++] = texture.channel || 0, array[offset + i++] = elements[1], array[offset + i++] = elements[4], array[offset + i++] = elements[7], i++;
      }
      return 8;
    }
    let index = 0, pixelCount = materials.length * MATERIAL_PIXELS, dimension = Math.ceil(Math.sqrt(pixelCount)) || 1, { image, features } = this, textureLookUp = {};
    for (let i = 0, l = textures.length; i < l; i++)
      textureLookUp[getTextureHash(textures[i])] = i;
    image.width !== dimension && (this.dispose(), image.data = new Float32Array(dimension * dimension * 4), image.width = dimension, image.height = dimension);
    let floatArray = image.data;
    features.reset();
    for (let i = 0, l = materials.length; i < l; i++) {
      let m2 = materials[i];
      if (m2.isFogVolumeMaterial) {
        features.setUsed("FOG");
        for (let j = 0; j < MATERIAL_STRIDE; j++)
          floatArray[index + j] = 0;
        floatArray[index + 0 + 0] = m2.color.r, floatArray[index + 0 + 1] = m2.color.g, floatArray[index + 0 + 2] = m2.color.b, floatArray[index + 8 + 3] = getField(m2, "emissiveIntensity", 0), floatArray[index + 12 + 0] = m2.emissive.r, floatArray[index + 12 + 1] = m2.emissive.g, floatArray[index + 12 + 2] = m2.emissive.b, floatArray[index + 52 + 1] = m2.density, floatArray[index + 52 + 3] = 0, floatArray[index + 56 + 2] = 4, index += MATERIAL_STRIDE;
        continue;
      }
      floatArray[index++] = m2.color.r, floatArray[index++] = m2.color.g, floatArray[index++] = m2.color.b, floatArray[index++] = getTexture(m2, "map"), floatArray[index++] = getField(m2, "metalness", 0), floatArray[index++] = getTexture(m2, "metalnessMap"), floatArray[index++] = getField(m2, "roughness", 0), floatArray[index++] = getTexture(m2, "roughnessMap"), floatArray[index++] = getField(m2, "ior", 1.5), floatArray[index++] = getField(m2, "transmission", 0), floatArray[index++] = getTexture(m2, "transmissionMap"), floatArray[index++] = getField(m2, "emissiveIntensity", 0), "emissive" in m2 ? (floatArray[index++] = m2.emissive.r, floatArray[index++] = m2.emissive.g, floatArray[index++] = m2.emissive.b) : (floatArray[index++] = 0, floatArray[index++] = 0, floatArray[index++] = 0), floatArray[index++] = getTexture(m2, "emissiveMap"), floatArray[index++] = getTexture(m2, "normalMap"), "normalScale" in m2 ? (floatArray[index++] = m2.normalScale.x, floatArray[index++] = m2.normalScale.y) : (floatArray[index++] = 1, floatArray[index++] = 1), floatArray[index++] = getField(m2, "clearcoat", 0), floatArray[index++] = getTexture(m2, "clearcoatMap"), floatArray[index++] = getField(m2, "clearcoatRoughness", 0), floatArray[index++] = getTexture(m2, "clearcoatRoughnessMap"), floatArray[index++] = getTexture(m2, "clearcoatNormalMap"), "clearcoatNormalScale" in m2 ? (floatArray[index++] = m2.clearcoatNormalScale.x, floatArray[index++] = m2.clearcoatNormalScale.y) : (floatArray[index++] = 1, floatArray[index++] = 1), index++, floatArray[index++] = getField(m2, "sheen", 0), "sheenColor" in m2 ? (floatArray[index++] = m2.sheenColor.r, floatArray[index++] = m2.sheenColor.g, floatArray[index++] = m2.sheenColor.b) : (floatArray[index++] = 0, floatArray[index++] = 0, floatArray[index++] = 0), floatArray[index++] = getTexture(m2, "sheenColorMap"), floatArray[index++] = getField(m2, "sheenRoughness", 0), floatArray[index++] = getTexture(m2, "sheenRoughnessMap"), floatArray[index++] = getTexture(m2, "iridescenceMap"), floatArray[index++] = getTexture(m2, "iridescenceThicknessMap"), floatArray[index++] = getField(m2, "iridescence", 0), floatArray[index++] = getField(m2, "iridescenceIOR", 1.3);
      let iridescenceThicknessRange = getField(m2, "iridescenceThicknessRange", [100, 400]);
      floatArray[index++] = iridescenceThicknessRange[0], floatArray[index++] = iridescenceThicknessRange[1], "specularColor" in m2 ? (floatArray[index++] = m2.specularColor.r, floatArray[index++] = m2.specularColor.g, floatArray[index++] = m2.specularColor.b) : (floatArray[index++] = 1, floatArray[index++] = 1, floatArray[index++] = 1), floatArray[index++] = getTexture(m2, "specularColorMap"), floatArray[index++] = getField(m2, "specularIntensity", 1), floatArray[index++] = getTexture(m2, "specularIntensityMap");
      let isThinFilm = getField(m2, "thickness", 0) === 0 && getField(m2, "attenuationDistance", 1 / 0) === 1 / 0;
      if (floatArray[index++] = Number(isThinFilm), index++, "attenuationColor" in m2 ? (floatArray[index++] = m2.attenuationColor.r, floatArray[index++] = m2.attenuationColor.g, floatArray[index++] = m2.attenuationColor.b) : (floatArray[index++] = 1, floatArray[index++] = 1, floatArray[index++] = 1), floatArray[index++] = getField(m2, "attenuationDistance", 1 / 0), floatArray[index++] = getTexture(m2, "alphaMap"), floatArray[index++] = m2.opacity, floatArray[index++] = m2.alphaTest, !isThinFilm && m2.transmission > 0)
        floatArray[index++] = 0;
      else
        switch (m2.side) {
          case FrontSide2:
            floatArray[index++] = 1;
            break;
          case BackSide2:
            floatArray[index++] = -1;
            break;
          case DoubleSide2:
            floatArray[index++] = 0;
            break;
        }
      floatArray[index++] = Number(getField(m2, "matte", !1)), floatArray[index++] = Number(getField(m2, "castShadow", !0)), floatArray[index++] = Number(m2.vertexColors) | Number(m2.flatShading) << 1, floatArray[index++] = Number(m2.transparent), index += writeTextureMatrixToArray(m2, "map", floatArray, index), index += writeTextureMatrixToArray(m2, "metalnessMap", floatArray, index), index += writeTextureMatrixToArray(m2, "roughnessMap", floatArray, index), index += writeTextureMatrixToArray(m2, "transmissionMap", floatArray, index), index += writeTextureMatrixToArray(m2, "emissiveMap", floatArray, index), index += writeTextureMatrixToArray(m2, "normalMap", floatArray, index), index += writeTextureMatrixToArray(m2, "clearcoatMap", floatArray, index), index += writeTextureMatrixToArray(m2, "clearcoatNormalMap", floatArray, index), index += writeTextureMatrixToArray(m2, "clearcoatRoughnessMap", floatArray, index), index += writeTextureMatrixToArray(m2, "sheenColorMap", floatArray, index), index += writeTextureMatrixToArray(m2, "sheenRoughnessMap", floatArray, index), index += writeTextureMatrixToArray(m2, "iridescenceMap", floatArray, index), index += writeTextureMatrixToArray(m2, "iridescenceThicknessMap", floatArray, index), index += writeTextureMatrixToArray(m2, "specularColorMap", floatArray, index), index += writeTextureMatrixToArray(m2, "specularIntensityMap", floatArray, index), index += writeTextureMatrixToArray(m2, "alphaMap", floatArray, index);
      // Six texels carry procedural settings, linear color stops, and the
      // three rows mapping a world-space hit into the object's Generated space.
      const wood=m2.userData.wuyingWood;
      floatArray.fill(0,index,index+24);
      if(wood){
        floatArray.set([wood.scale,wood.rotation,wood.range?.[0] ?? -1,wood.range?.[1] ?? -1],index);
        if(wood.colors){
          floatArray.set(wood.colors[0],index+4);
          floatArray[index+7]=1;
          floatArray.set(wood.colors[1],index+8);
        }
        floatArray[index+11]=wood.bumpDistance;
        for(let row=0;row<3;row++)for(let col=0;col<4;col++)
          floatArray[index+12+row*4+col]=wood.worldToGenerated[col*4+row];
      }
      index+=24;
    }
    let hash = bufferToHash(floatArray.buffer);
    return this.hash !== hash ? (this.hash = hash, this.needsUpdate = !0, !0) : !1;
  }
};

// npm/three-gpu-pathtracer/src/uniforms/RenderTarget2DArray.js
import {
  WebGLArrayRenderTarget,
  RGBAFormat as RGBAFormat8,
  UnsignedByteType as UnsignedByteType2,
  Color,
  RepeatWrapping as RepeatWrapping2,
  LinearFilter as LinearFilter2,
  NoToneMapping,
  ShaderMaterial as ShaderMaterial2
} from "../three/build/three.module.js";
var prevColor = new Color();
function getTextureHash2(texture) {
  return texture ? `${texture.uuid}:${texture.version}` : null;
}
function assignOptions(target2, options) {
  for (let key in options)
    key in target2 && (target2[key] = options[key]);
}
var RenderTarget2DArray = class extends WebGLArrayRenderTarget {
  constructor(width, height, options) {
    let textureOptions = {
      format: RGBAFormat8,
      type: UnsignedByteType2,
      minFilter: LinearFilter2,
      magFilter: LinearFilter2,
      wrapS: RepeatWrapping2,
      wrapT: RepeatWrapping2,
      generateMipmaps: !1,
      ...options
    };
    super(width, height, 1, textureOptions), assignOptions(this.texture, textureOptions), this.texture.setTextures = (...args) => {
      this.setTextures(...args);
    }, this.hashes = [null];
    let fsQuad = new FullScreenQuad(new CopyMaterial());
    this.fsQuad = fsQuad;
  }
  setTextures(renderer, textures, width = this.width, height = this.height) {
    let prevRenderTarget = renderer.getRenderTarget(), prevToneMapping = renderer.toneMapping, prevAlpha = renderer.getClearAlpha();
    renderer.getClearColor(prevColor);
    let depth = textures.length || 1;
    (width !== this.width || height !== this.height || this.depth !== depth) && (this.setSize(width, height, depth), this.hashes = new Array(depth).fill(null)), renderer.setClearColor(0, 0), renderer.toneMapping = NoToneMapping;
    let fsQuad = this.fsQuad, hashes = this.hashes, updated = !1;
    for (let i = 0, l = depth; i < l; i++) {
      let texture = textures[i], hash = getTextureHash2(texture);
      texture && (hashes[i] !== hash || texture.isWebGLRenderTarget) && (fsQuad.material.map = texture, renderer.setRenderTarget(this, i), fsQuad.render(renderer), hashes[i] = hash, updated = !0);
    }
    return fsQuad.material.map = null, renderer.setClearColor(prevColor, prevAlpha), renderer.setRenderTarget(prevRenderTarget), renderer.toneMapping = prevToneMapping, updated;
  }
  dispose() {
    super.dispose(), this.fsQuad.dispose();
  }
}, CopyMaterial = class extends ShaderMaterial2 {
  get map() {
    return this.uniforms.map.value;
  }
  set map(v2) {
    this.uniforms.map.value = v2;
  }
  constructor() {
    super({
      uniforms: {
        map: { value: null }
      },
      vertexShader: (
        /* glsl */
        `
				varying vec2 vUv;
				void main() {

					vUv = uv;
					gl_Position = projectionMatrix * modelViewMatrix * vec4( position, 1.0 );

				}
			`
      ),
      fragmentShader: (
        /* glsl */
        `
				uniform sampler2D map;
				varying vec2 vUv;
				void main() {

					gl_FragColor = texture2D( map, vUv );

				}
			`
      )
    });
  }
};

// npm/three-gpu-pathtracer/src/uniforms/StratifiedSamplesTexture.js
import { DataTexture as DataTexture6, FloatType as FloatType8, NearestFilter as NearestFilter6, RGBAFormat as RGBAFormat9 } from "../three/build/three.module.js";

// npm/three-gpu-pathtracer/src/uniforms/stratified/StratifiedSampler.js
function shuffle(arr, random = Math.random()) {
  for (let i = arr.length - 1; i > 0; i--) {
    let j = Math.floor(random() * (i + 1)), x = arr[i];
    arr[i] = arr[j], arr[j] = x;
  }
  return arr;
}
var StratifiedSampler = class {
  constructor(strataCount, dimensions, random = Math.random) {
    let l = strataCount ** dimensions, strata = new Uint16Array(l), index = l;
    for (let i = 0; i < l; i++)
      strata[i] = i;
    this.samples = new Float32Array(dimensions), this.strataCount = strataCount, this.reset = function() {
      for (let i = 0; i < l; i++)
        strata[i] = i;
      index = 0;
    }, this.reshuffle = function() {
      index = 0;
    }, this.next = function() {
      let { samples } = this;
      index >= strata.length && (shuffle(strata, random), this.reshuffle());
      let stratum = strata[index++];
      for (let i = 0; i < dimensions; i++)
        samples[i] = (stratum % strataCount + random()) / strataCount, stratum = Math.floor(stratum / strataCount);
      return samples;
    };
  }
};

// npm/three-gpu-pathtracer/src/uniforms/stratified/StratifiedSamplerCombined.js
var StratifiedSamplerCombined = class {
  constructor(strataCount, listOfDimensions, random = Math.random) {
    let totalDim = 0;
    for (let dim of listOfDimensions)
      totalDim += dim;
    let combined = new Float32Array(totalDim), strataObjs = [], offset = 0;
    for (let dim of listOfDimensions) {
      let sampler = new StratifiedSampler(strataCount, dim, random);
      sampler.samples = new Float32Array(combined.buffer, offset, sampler.samples.length), offset += sampler.samples.length * 4, strataObjs.push(sampler);
    }
    this.samples = combined, this.strataCount = strataCount, this.next = function() {
      for (let strata of strataObjs)
        strata.next();
      return combined;
    }, this.reshuffle = function() {
      for (let strata of strataObjs)
        strata.reshuffle();
    }, this.reset = function() {
      for (let strata of strataObjs)
        strata.reset();
    };
  }
};

// npm/three-gpu-pathtracer/src/uniforms/StratifiedSamplesTexture.js
var RandomGenerator = class {
  constructor(seed = 0) {
    this.m = 2147483648, this.a = 1103515245, this.c = 12345, this.seed = seed;
  }
  nextInt() {
    return this.seed = (this.a * this.seed + this.c) % this.m, this.seed;
  }
  nextFloat() {
    return this.nextInt() / (this.m - 1);
  }
}, StratifiedSamplesTexture = class extends DataTexture6 {
  constructor(count = 1, depth = 1, strata = 8) {
    super(new Float32Array(1), 1, 1, RGBAFormat9, FloatType8), this.minFilter = NearestFilter6, this.magFilter = NearestFilter6, this.strata = strata, this.sampler = null, this.generator = new RandomGenerator(), this.stableNoise = !1, this.random = () => this.stableNoise ? this.generator.nextFloat() : Math.random(), this.init(count, depth, strata);
  }
  init(count = this.image.height, depth = this.image.width, strata = this.strata) {
    let { image } = this;
    if (image.width === depth && image.height === count && this.sampler !== null)
      return;
    let dimensions = new Array(count * depth).fill(4), sampler = new StratifiedSamplerCombined(strata, dimensions, this.random);
    image.width = depth, image.height = count, image.data = sampler.samples, this.sampler = sampler, this.dispose(), this.next();
  }
  next() {
    this.sampler.next(), this.needsUpdate = !0;
  }
  reset() {
    this.sampler.reset(), this.generator.seed = 0;
  }
};

// npm/three-gpu-pathtracer/src/textures/BlueNoiseTexture.js
import { DataTexture as DataTexture7, FloatType as FloatType9, NearestFilter as NearestFilter7, RGBAFormat as RGBAFormat10, RGFormat as RGFormat2, RedFormat as RedFormat3 } from "../three/build/three.module.js";

// npm/three-gpu-pathtracer/src/textures/blueNoise/utils.js
function shuffleArray(array, random = Math.random) {
  for (let i = array.length - 1; i > 0; i--) {
    let replaceIndex = ~~((random() - 1e-6) * i), tmp = array[i];
    array[i] = array[replaceIndex], array[replaceIndex] = tmp;
  }
}
function fillWithOnes(array, count) {
  array.fill(0);
  for (let i = 0; i < count; i++)
    array[i] = 1;
}

// npm/three-gpu-pathtracer/src/textures/blueNoise/BlueNoiseSamples.js
var BlueNoiseSamples = class {
  constructor(size) {
    this.count = 0, this.size = -1, this.sigma = -1, this.radius = -1, this.lookupTable = null, this.score = null, this.binaryPattern = null, this.resize(size), this.setSigma(1.5);
  }
  findVoid() {
    let { score, binaryPattern } = this, currValue = 1 / 0, currIndex = -1;
    for (let i = 0, l = binaryPattern.length; i < l; i++) {
      if (binaryPattern[i] !== 0)
        continue;
      let pScore = score[i];
      pScore < currValue && (currValue = pScore, currIndex = i);
    }
    return currIndex;
  }
  findCluster() {
    let { score, binaryPattern } = this, currValue = -1 / 0, currIndex = -1;
    for (let i = 0, l = binaryPattern.length; i < l; i++) {
      if (binaryPattern[i] !== 1)
        continue;
      let pScore = score[i];
      pScore > currValue && (currValue = pScore, currIndex = i);
    }
    return currIndex;
  }
  setSigma(sigma) {
    if (sigma === this.sigma)
      return;
    let radius = ~~(Math.sqrt(20 * sigma ** 2) + 1), lookupWidth = 2 * radius + 1, lookupTable = new Float32Array(lookupWidth * lookupWidth), sigma2 = sigma * sigma;
    for (let x = -radius; x <= radius; x++)
      for (let y = -radius; y <= radius; y++) {
        let index = (radius + y) * lookupWidth + x + radius, dist2 = x * x + y * y;
        lookupTable[index] = Math.E ** (-dist2 / (2 * sigma2));
      }
    this.lookupTable = lookupTable, this.sigma = sigma, this.radius = radius;
  }
  resize(size) {
    this.size !== size && (this.size = size, this.score = new Float32Array(size * size), this.binaryPattern = new Uint8Array(size * size));
  }
  invert() {
    let { binaryPattern, score, size } = this;
    score.fill(0);
    for (let i = 0, l = binaryPattern.length; i < l; i++)
      if (binaryPattern[i] === 0) {
        let y = ~~(i / size), x = i - y * size;
        this.updateScore(x, y, 1), binaryPattern[i] = 1;
      } else
        binaryPattern[i] = 0;
  }
  updateScore(x, y, multiplier) {
    let { size, score, lookupTable } = this, radius = this.radius, lookupWidth = 2 * radius + 1;
    for (let px = -radius; px <= radius; px++)
      for (let py = -radius; py <= radius; py++) {
        let lookupIndex = (radius + py) * lookupWidth + px + radius, value = lookupTable[lookupIndex], sx = x + px;
        sx = sx < 0 ? size + sx : sx % size;
        let sy = y + py;
        sy = sy < 0 ? size + sy : sy % size;
        let sindex = sy * size + sx;
        score[sindex] += multiplier * value;
      }
  }
  addPointIndex(index) {
    this.binaryPattern[index] = 1;
    let size = this.size, y = ~~(index / size), x = index - y * size;
    this.updateScore(x, y, 1), this.count++;
  }
  removePointIndex(index) {
    this.binaryPattern[index] = 0;
    let size = this.size, y = ~~(index / size), x = index - y * size;
    this.updateScore(x, y, -1), this.count--;
  }
  copy(source) {
    this.resize(source.size), this.score.set(source.score), this.binaryPattern.set(source.binaryPattern), this.setSigma(source.sigma), this.count = source.count;
  }
};

// npm/three-gpu-pathtracer/src/textures/blueNoise/BlueNoiseGenerator.js
var BlueNoiseGenerator = class {
  constructor() {
    this.random = Math.random, this.sigma = 1.5, this.size = 64, this.majorityPointsRatio = 0.1, this.samples = new BlueNoiseSamples(1), this.savedSamples = new BlueNoiseSamples(1);
  }
  generate() {
    let {
      samples,
      savedSamples,
      sigma,
      majorityPointsRatio,
      size
    } = this;
    samples.resize(size), samples.setSigma(sigma);
    let pointCount = Math.floor(size * size * majorityPointsRatio), initialSamples = samples.binaryPattern;
    fillWithOnes(initialSamples, pointCount), shuffleArray(initialSamples, this.random);
    for (let i = 0, l = initialSamples.length; i < l; i++)
      initialSamples[i] === 1 && samples.addPointIndex(i);
    for (; ; ) {
      let clusterIndex = samples.findCluster();
      samples.removePointIndex(clusterIndex);
      let voidIndex = samples.findVoid();
      if (clusterIndex === voidIndex) {
        samples.addPointIndex(clusterIndex);
        break;
      }
      samples.addPointIndex(voidIndex);
    }
    let ditherArray = new Uint32Array(size * size);
    savedSamples.copy(samples);
    let rank;
    for (rank = samples.count - 1; rank >= 0; ) {
      let clusterIndex = samples.findCluster();
      samples.removePointIndex(clusterIndex), ditherArray[clusterIndex] = rank, rank--;
    }
    let totalSize = size * size;
    for (rank = savedSamples.count; rank < totalSize / 2; ) {
      let voidIndex = savedSamples.findVoid();
      savedSamples.addPointIndex(voidIndex), ditherArray[voidIndex] = rank, rank++;
    }
    for (savedSamples.invert(); rank < totalSize; ) {
      let clusterIndex = savedSamples.findCluster();
      savedSamples.removePointIndex(clusterIndex), ditherArray[clusterIndex] = rank, rank++;
    }
    return { data: ditherArray, maxValue: totalSize };
  }
};

// npm/three-gpu-pathtracer/src/textures/BlueNoiseTexture.js
function getStride(channels) {
  return channels >= 3 ? 4 : channels;
}
function getFormat(channels) {
  switch (channels) {
    case 1:
      return RedFormat3;
    case 2:
      return RGFormat2;
    default:
      return RGBAFormat10;
  }
}
var BlueNoiseTexture = class extends DataTexture7 {
  constructor(size = 64, channels = 1) {
    super(new Float32Array(4), 1, 1, RGBAFormat10, FloatType9), this.minFilter = NearestFilter7, this.magFilter = NearestFilter7, this.size = size, this.channels = channels, this.update();
  }
  update() {
    let channels = this.channels, size = this.size, generator = new BlueNoiseGenerator();
    generator.channels = channels, generator.size = size;
    let stride = getStride(channels), format = getFormat(stride);
    (this.image.width !== size || format !== this.format) && (this.image.width = size, this.image.height = size, this.image.data = new Float32Array(size ** 2 * stride), this.format = format, this.dispose());
    let data = this.image.data;
    for (let i = 0, l = channels; i < l; i++) {
      let result = generator.generate(), bin = result.data, maxValue = result.maxValue;
      for (let j = 0, l2 = bin.length; j < l2; j++) {
        let value = bin[j] / maxValue;
        data[j * stride + i] = value;
      }
    }
    this.needsUpdate = !0;
  }
};

// npm/three-gpu-pathtracer/src/shader/structs/camera_struct.glsl.js
var camera_struct = (
  /* glsl */
  `

	struct PhysicalCamera {

		float focusDistance;
		float anamorphicRatio;
		float bokehSize;
		int apertureBlades;
		float apertureRotation;

	};

`
);

// npm/three-gpu-pathtracer/src/shader/structs/equirect_struct.glsl.js
var equirect_struct = (
  /* glsl */
  `

	struct EquirectHdrInfo {

		sampler2D marginalWeights;
		sampler2D conditionalWeights;
		sampler2D map;

		float totalSum;

	};

`
);

// npm/three-gpu-pathtracer/src/shader/structs/lights_struct.glsl.js
var lights_struct = (
  /* glsl */
  `

	#define RECT_AREA_LIGHT_TYPE 0
	#define CIRC_AREA_LIGHT_TYPE 1
	#define SPOT_LIGHT_TYPE 2
	#define DIR_LIGHT_TYPE 3
	#define POINT_LIGHT_TYPE 4

	struct LightsInfo {

		sampler2D tex;
		uint count;

	};

	struct Light {

		vec3 position;
		int type;

		vec3 color;
		float intensity;

		vec3 u;
		vec3 v;
		float area;

		// spot light fields
		float radius;
		float near;
		float decay;
		float distance;
		float coneCos;
		float penumbraCos;
		int iesProfile;

	};

	Light readLightInfo( sampler2D tex, uint index ) {

		uint i = index * 6u;

		vec4 s0 = texelFetch1D( tex, i + 0u );
		vec4 s1 = texelFetch1D( tex, i + 1u );
		vec4 s2 = texelFetch1D( tex, i + 2u );
		vec4 s3 = texelFetch1D( tex, i + 3u );

		Light l;
		l.position = s0.rgb;
		l.type = int( round( s0.a ) );

		l.color = s1.rgb;
		l.intensity = s1.a;

		l.u = s2.rgb;
		l.v = s3.rgb;
		l.area = s3.a;

		if ( l.type == SPOT_LIGHT_TYPE || l.type == POINT_LIGHT_TYPE ) {

			vec4 s4 = texelFetch1D( tex, i + 4u );
			vec4 s5 = texelFetch1D( tex, i + 5u );
			l.radius = s4.r;
			l.decay = s4.g;
			l.distance = s4.b;
			l.coneCos = s4.a;

			l.penumbraCos = s5.r;
			l.iesProfile = int( round( s5.g ) );

		} else {

			l.radius = 0.0;
			l.decay = 0.0;
			l.distance = 0.0;

			l.coneCos = 0.0;
			l.penumbraCos = 0.0;
			l.iesProfile = - 1;

		}

		return l;

	}

`
);

// npm/three-gpu-pathtracer/src/shader/structs/material_struct.glsl.js
var material_struct = (
  /* glsl */
  `

	struct Material {
		#if FEATURE_WUYING_WOOD
		vec4 woodSettings;
		uint woodOffset;
		#endif

		vec3 color;
		int map;

		float metalness;
		int metalnessMap;

		float roughness;
		int roughnessMap;

		float ior;
		float transmission;
		int transmissionMap;

		float emissiveIntensity;
		vec3 emissive;
		int emissiveMap;

		int normalMap;
		vec2 normalScale;

		float clearcoat;
		int clearcoatMap;
		int clearcoatNormalMap;
		vec2 clearcoatNormalScale;
		float clearcoatRoughness;
		int clearcoatRoughnessMap;

		int iridescenceMap;
		int iridescenceThicknessMap;
		float iridescence;
		float iridescenceIor;
		float iridescenceThicknessMinimum;
		float iridescenceThicknessMaximum;

		vec3 specularColor;
		int specularColorMap;

		float specularIntensity;
		int specularIntensityMap;
		bool thinFilm;

		vec3 attenuationColor;
		float attenuationDistance;

		int alphaMap;

		bool castShadow;
		float opacity;
		float alphaTest;

		float side;
		bool matte;

		float sheen;
		vec3 sheenColor;
		int sheenColorMap;
		float sheenRoughness;
		int sheenRoughnessMap;

		bool vertexColors;
		bool flatShading;
		bool transparent;
		bool fogVolume;

		mat3 mapTransform;
		mat3 metalnessMapTransform;
		mat3 roughnessMapTransform;
		mat3 transmissionMapTransform;
		mat3 emissiveMapTransform;
		mat3 normalMapTransform;
		mat3 clearcoatMapTransform;
		mat3 clearcoatNormalMapTransform;
		mat3 clearcoatRoughnessMapTransform;
		mat3 sheenColorMapTransform;
		mat3 sheenRoughnessMapTransform;
		mat3 iridescenceMapTransform;
		mat3 iridescenceThicknessMapTransform;
		mat3 specularColorMapTransform;
		mat3 specularIntensityMapTransform;
		mat3 alphaMapTransform;

	};

	mat3 readTextureTransform( sampler2D tex, uint index ) {

		mat3 textureTransform;

		vec4 row1 = texelFetch1D( tex, index );
		vec4 row2 = texelFetch1D( tex, index + 1u );

		// The spare component carries this map's UV channel. It only changes
		// the unused third component of the affine UV transform's result.
		textureTransform[0] = vec3(row1.r, row2.r, row1.a);
		textureTransform[1] = vec3(row1.g, row2.g, 0.0);
		textureTransform[2] = vec3(row1.b, row2.b, 1.0);

		return textureTransform;

	}

	Material readMaterialInfo( sampler2D tex, uint index ) {

		uint i = index * uint( MATERIAL_PIXELS );

		vec4 s0 = texelFetch1D( tex, i + 0u );
		vec4 s1 = texelFetch1D( tex, i + 1u );
		vec4 s2 = texelFetch1D( tex, i + 2u );
		vec4 s3 = texelFetch1D( tex, i + 3u );
		vec4 s4 = texelFetch1D( tex, i + 4u );
		vec4 s5 = texelFetch1D( tex, i + 5u );
		vec4 s6 = texelFetch1D( tex, i + 6u );
		vec4 s7 = texelFetch1D( tex, i + 7u );
		vec4 s8 = texelFetch1D( tex, i + 8u );
		vec4 s9 = texelFetch1D( tex, i + 9u );
		vec4 s10 = texelFetch1D( tex, i + 10u );
		vec4 s11 = texelFetch1D( tex, i + 11u );
		vec4 s12 = texelFetch1D( tex, i + 12u );
		vec4 s13 = texelFetch1D( tex, i + 13u );
		vec4 s14 = texelFetch1D( tex, i + 14u );

		Material m;
		#if FEATURE_WUYING_WOOD
		m.woodSettings = texelFetch1D( tex, i + 47u );
		m.woodOffset = i + 48u;
		#endif
		m.color = s0.rgb;
		m.map = int( round( s0.a ) );

		m.metalness = s1.r;
		m.metalnessMap = int( round( s1.g ) );
		m.roughness = s1.b;
		m.roughnessMap = int( round( s1.a ) );

		m.ior = s2.r;
		m.transmission = s2.g;
		m.transmissionMap = int( round( s2.b ) );
		m.emissiveIntensity = s2.a;

		m.emissive = s3.rgb;
		m.emissiveMap = int( round( s3.a ) );

		m.normalMap = int( round( s4.r ) );
		m.normalScale = s4.gb;

		m.clearcoat = s4.a;
		m.clearcoatMap = int( round( s5.r ) );
		m.clearcoatRoughness = s5.g;
		m.clearcoatRoughnessMap = int( round( s5.b ) );
		m.clearcoatNormalMap = int( round( s5.a ) );
		m.clearcoatNormalScale = s6.rg;

		m.sheen = s6.a;
		m.sheenColor = s7.rgb;
		m.sheenColorMap = int( round( s7.a ) );
		m.sheenRoughness = s8.r;
		m.sheenRoughnessMap = int( round( s8.g ) );

		m.iridescenceMap = int( round( s8.b ) );
		m.iridescenceThicknessMap = int( round( s8.a ) );
		m.iridescence = s9.r;
		m.iridescenceIor = s9.g;
		m.iridescenceThicknessMinimum = s9.b;
		m.iridescenceThicknessMaximum = s9.a;

		m.specularColor = s10.rgb;
		m.specularColorMap = int( round( s10.a ) );

		m.specularIntensity = s11.r;
		m.specularIntensityMap = int( round( s11.g ) );
		m.thinFilm = bool( s11.b );

		m.attenuationColor = s12.rgb;
		m.attenuationDistance = s12.a;

		m.alphaMap = int( round( s13.r ) );

		m.opacity = s13.g;
		m.alphaTest = s13.b;
		m.side = s13.a;

		m.matte = bool( s14.r );
		m.castShadow = bool( s14.g );
		m.vertexColors = bool( int( s14.b ) & 1 );
		m.flatShading = bool( int( s14.b ) & 2 );
		m.fogVolume = bool( int( s14.b ) & 4 );
		m.transparent = bool( s14.a );

		uint firstTextureTransformIdx = i + 15u;

		// mat3( 1.0 ) is an identity matrix
		m.mapTransform = m.map == - 1 ? mat3( 1.0 ) : readTextureTransform( tex, firstTextureTransformIdx );
		m.metalnessMapTransform = m.metalnessMap == - 1 ? mat3( 1.0 ) : readTextureTransform( tex, firstTextureTransformIdx + 2u );
		m.roughnessMapTransform = m.roughnessMap == - 1 ? mat3( 1.0 ) : readTextureTransform( tex, firstTextureTransformIdx + 4u );
		m.transmissionMapTransform = m.transmissionMap == - 1 ? mat3( 1.0 ) : readTextureTransform( tex, firstTextureTransformIdx + 6u );
		m.emissiveMapTransform = m.emissiveMap == - 1 ? mat3( 1.0 ) : readTextureTransform( tex, firstTextureTransformIdx + 8u );
		m.normalMapTransform = m.normalMap == - 1 ? mat3( 1.0 ) : readTextureTransform( tex, firstTextureTransformIdx + 10u );
		m.clearcoatMapTransform = m.clearcoatMap == - 1 ? mat3( 1.0 ) : readTextureTransform( tex, firstTextureTransformIdx + 12u );
		m.clearcoatNormalMapTransform = m.clearcoatNormalMap == - 1 ? mat3( 1.0 ) : readTextureTransform( tex, firstTextureTransformIdx + 14u );
		m.clearcoatRoughnessMapTransform = m.clearcoatRoughnessMap == - 1 ? mat3( 1.0 ) : readTextureTransform( tex, firstTextureTransformIdx + 16u );
		m.sheenColorMapTransform = m.sheenColorMap == - 1 ? mat3( 1.0 ) : readTextureTransform( tex, firstTextureTransformIdx + 18u );
		m.sheenRoughnessMapTransform = m.sheenRoughnessMap == - 1 ? mat3( 1.0 ) : readTextureTransform( tex, firstTextureTransformIdx + 20u );
		m.iridescenceMapTransform = m.iridescenceMap == - 1 ? mat3( 1.0 ) : readTextureTransform( tex, firstTextureTransformIdx + 22u );
		m.iridescenceThicknessMapTransform = m.iridescenceThicknessMap == - 1 ? mat3( 1.0 ) : readTextureTransform( tex, firstTextureTransformIdx + 24u );
		m.specularColorMapTransform = m.specularColorMap == - 1 ? mat3( 1.0 ) : readTextureTransform( tex, firstTextureTransformIdx + 26u );
		m.specularIntensityMapTransform = m.specularIntensityMap == - 1 ? mat3( 1.0 ) : readTextureTransform( tex, firstTextureTransformIdx + 28u );
		m.alphaMapTransform = m.alphaMap == - 1 ? mat3( 1.0 ) : readTextureTransform( tex, firstTextureTransformIdx + 30u );

		return m;

	}

`
);

// npm/three-gpu-pathtracer/src/shader/structs/surface_record_struct.glsl.js
var surface_record_struct = (
  /* glsl */
  `

	struct SurfaceRecord {

		// surface type
		bool volumeParticle;

		// geometry
		vec3 faceNormal;
		bool frontFace;
		vec3 normal;
		mat3 normalBasis;
		mat3 normalInvBasis;

		// cached properties
		float eta;
		float f0;

		// material
		float roughness;
		float filteredRoughness;
		float metalness;
		vec3 color;
		vec3 emission;

		// transmission
		float ior;
		float transmission;
		bool thinFilm;
		vec3 attenuationColor;
		float attenuationDistance;

		// clearcoat
		vec3 clearcoatNormal;
		mat3 clearcoatBasis;
		mat3 clearcoatInvBasis;
		float clearcoat;
		float clearcoatRoughness;
		float filteredClearcoatRoughness;

		// sheen
		float sheen;
		vec3 sheenColor;
		float sheenRoughness;

		// iridescence
		float iridescence;
		float iridescenceIor;
		float iridescenceThickness;

		// specular
		vec3 specularColor;
		float specularIntensity;
	};

	struct ScatterRecord {
		float specularPdf;
		float pdf;
		vec3 direction;
		vec3 color;
	};

`
);

// npm/three-gpu-pathtracer/src/shader/sampling/equirect_sampling_functions.glsl.js
var equirect_functions = (
  /* glsl */
  `

	// samples the the given environment map in the given direction
	vec3 sampleEquirectColor( sampler2D envMap, vec3 direction ) {

		return texture2D( envMap, equirectDirectionToUv( direction ) ).rgb;

	}

	// gets the pdf of the given direction to sample
	float equirectDirectionPdf( vec3 direction ) {

		vec2 uv = equirectDirectionToUv( direction );
		float theta = uv.y * PI;
		float sinTheta = sin( theta );
		if ( sinTheta == 0.0 ) {

			return 0.0;

		}

		return 1.0 / ( 2.0 * PI * PI * sinTheta );

	}

	// samples the color given env map with CDF and returns the pdf of the direction
	float sampleEquirect( vec3 direction, inout vec3 color ) {

		float totalSum = envMapInfo.totalSum;
		if ( totalSum == 0.0 ) {

			color = vec3( 0.0 );
			return 1.0;

		}

		vec2 uv = equirectDirectionToUv( direction );
		color = texture2D( envMapInfo.map, uv ).rgb;

		float lum = luminance( color );
		ivec2 resolution = textureSize( envMapInfo.map, 0 );
		float pdf = lum / totalSum;

		return float( resolution.x * resolution.y ) * pdf * equirectDirectionPdf( direction );

	}

	// samples a direction of the envmap with color and retrieves pdf
	float sampleEquirectProbability( vec2 r, inout vec3 color, inout vec3 direction ) {

		// sample env map cdf
		float v = texture2D( envMapInfo.marginalWeights, vec2( r.x, 0.0 ) ).x;
		float u = texture2D( envMapInfo.conditionalWeights, vec2( r.y, v ) ).x;
		vec2 uv = vec2( u, v );

		vec3 derivedDirection = equirectUvToDirection( uv );
		direction = derivedDirection;
		color = texture2D( envMapInfo.map, uv ).rgb;

		float totalSum = envMapInfo.totalSum;
		float lum = luminance( color );
		ivec2 resolution = textureSize( envMapInfo.map, 0 );
		float pdf = lum / totalSum;

		return float( resolution.x * resolution.y ) * pdf * equirectDirectionPdf( direction );

	}
`
);

// npm/three-gpu-pathtracer/src/shader/sampling/light_sampling_functions.glsl.js
var light_sampling_functions = (
  /* glsl */
  `

	float getSpotAttenuation( const in float coneCosine, const in float penumbraCosine, const in float angleCosine ) {

		return smoothstep( coneCosine, penumbraCosine, angleCosine );

	}

	float getDistanceAttenuation( const in float lightDistance, const in float cutoffDistance, const in float decayExponent ) {

		// based upon Frostbite 3 Moving to Physically-based Rendering
		// page 32, equation 26: E[window1]
		// https://seblagarde.files.wordpress.com/2015/07/course_notes_moving_frostbite_to_pbr_v32.pdf
		float distanceFalloff = 1.0 / max( pow( lightDistance, decayExponent ), EPSILON );

		if ( cutoffDistance > 0.0 ) {

			distanceFalloff *= pow2( saturate( 1.0 - pow4( lightDistance / cutoffDistance ) ) );

		}

		return distanceFalloff;

	}

	float getPhotometricAttenuation( sampler2DArray iesProfiles, int iesProfile, vec3 posToLight, vec3 lightDir, vec3 u, vec3 v ) {

		float cosTheta = dot( posToLight, lightDir );
		float angle = acos( cosTheta ) / PI;

		return texture2D( iesProfiles, vec3( angle, 0.0, iesProfile ) ).r;

	}

	struct LightRecord {

		float dist;
		vec3 direction;
		float pdf;
		vec3 emission;
		int type;

	};

	bool intersectLightAtIndex( sampler2D lights, vec3 rayOrigin, vec3 rayDirection, uint l, inout LightRecord lightRec ) {

		bool didHit = false;
		Light light = readLightInfo( lights, l );

		vec3 u = light.u;
		vec3 v = light.v;

		// check for backface
		vec3 normal = normalize( cross( u, v ) );
		if ( dot( normal, rayDirection ) > 0.0 ) {

			u *= 1.0 / dot( u, u );
			v *= 1.0 / dot( v, v );

			float dist;

			// MIS / light intersection is not supported for punctual lights.
			if(
				( light.type == RECT_AREA_LIGHT_TYPE && intersectsRectangle( light.position, normal, u, v, rayOrigin, rayDirection, dist ) ) ||
				( light.type == CIRC_AREA_LIGHT_TYPE && intersectsCircle( light.position, normal, u, v, rayOrigin, rayDirection, dist ) )
			) {

				float cosTheta = dot( rayDirection, normal );
				didHit = true;
				lightRec.dist = dist;
				lightRec.pdf = ( dist * dist ) / ( light.area * cosTheta );
				lightRec.emission = light.color * light.intensity;
				lightRec.direction = rayDirection;
				lightRec.type = light.type;

			}

		}

		return didHit;

	}

	LightRecord randomAreaLightSample( Light light, vec3 rayOrigin, vec2 ruv ) {

		vec3 randomPos;
		if( light.type == RECT_AREA_LIGHT_TYPE ) {

			// rectangular area light
			randomPos = light.position + light.u * ( ruv.x - 0.5 ) + light.v * ( ruv.y - 0.5 );

		} else if( light.type == CIRC_AREA_LIGHT_TYPE ) {

			// circular area light
			float r = 0.5 * sqrt( ruv.x );
			float theta = ruv.y * 2.0 * PI;
			float x = r * cos( theta );
			float y = r * sin( theta );

			randomPos = light.position + light.u * x + light.v * y;

		}

		vec3 toLight = randomPos - rayOrigin;
		float lightDistSq = dot( toLight, toLight );
		float dist = sqrt( lightDistSq );
		vec3 direction = toLight / dist;
		vec3 lightNormal = normalize( cross( light.u, light.v ) );

		LightRecord lightRec;
		lightRec.type = light.type;
		lightRec.emission = light.color * light.intensity;
		lightRec.dist = dist;
		lightRec.direction = direction;

		// TODO: the denominator is potentially zero
		lightRec.pdf = lightDistSq / ( light.area * dot( direction, lightNormal ) );

		return lightRec;

	}

	LightRecord randomSpotLightSample( Light light, sampler2DArray iesProfiles, vec3 rayOrigin, vec2 ruv ) {

		float radius = light.radius * sqrt( ruv.x );
		float theta = ruv.y * 2.0 * PI;
		float x = radius * cos( theta );
		float y = radius * sin( theta );

		vec3 u = light.u;
		vec3 v = light.v;
		vec3 normal = normalize( cross( u, v ) );

		float angle = acos( light.coneCos );
		float angleTan = tan( angle );
		float startDistance = light.radius / max( angleTan, EPSILON );

		vec3 randomPos = light.position - normal * startDistance + u * x + v * y;
		vec3 toLight = randomPos - rayOrigin;
		float lightDistSq = dot( toLight, toLight );
		float dist = sqrt( lightDistSq );

		vec3 direction = toLight / max( dist, EPSILON );
		float cosTheta = dot( direction, normal );

		float spotAttenuation = light.iesProfile != - 1 ?
			getPhotometricAttenuation( iesProfiles, light.iesProfile, direction, normal, u, v ) :
			getSpotAttenuation( light.coneCos, light.penumbraCos, cosTheta );

		float distanceAttenuation = getDistanceAttenuation( dist, light.distance, light.decay );
		LightRecord lightRec;
		lightRec.type = light.type;
		lightRec.dist = dist;
		lightRec.direction = direction;
		lightRec.emission = light.color * light.intensity * distanceAttenuation * spotAttenuation;
		lightRec.pdf = 1.0;

		return lightRec;

	}

	LightRecord randomLightSample( sampler2D lights, sampler2DArray iesProfiles, uint lightCount, vec3 rayOrigin, vec3 ruv ) {

		LightRecord result;

		// pick a random light
		uint l = uint( ruv.x * float( lightCount ) );
		Light light = readLightInfo( lights, l );

		if ( light.type == SPOT_LIGHT_TYPE ) {

			result = randomSpotLightSample( light, iesProfiles, rayOrigin, ruv.yz );

		} else if ( light.type == POINT_LIGHT_TYPE ) {

			vec3 lightRay = light.u - rayOrigin;
			float lightDist = length( lightRay );
			float cutoffDistance = light.distance;
			float distanceFalloff = 1.0 / max( pow( lightDist, light.decay ), 0.01 );
			if ( cutoffDistance > 0.0 ) {

				distanceFalloff *= pow2( saturate( 1.0 - pow4( lightDist / cutoffDistance ) ) );

			}

			LightRecord rec;
			rec.direction = normalize( lightRay );
			rec.dist = length( lightRay );
			rec.pdf = 1.0;
			rec.emission = light.color * light.intensity * distanceFalloff;
			rec.type = light.type;
			result = rec;

		} else if ( light.type == DIR_LIGHT_TYPE ) {

			LightRecord rec;
			rec.dist = 1e10;
			rec.direction = light.u;
			rec.pdf = 1.0;
			rec.emission = light.color * light.intensity;
			rec.type = light.type;

			result = rec;

		} else {

			// sample the light
			result = randomAreaLightSample( light, rayOrigin, ruv.yz );

		}

		return result;

	}

`
);

// npm/three-gpu-pathtracer/src/shader/sampling/shape_sampling_functions.glsl.js
var shape_sampling_functions = (
  /* glsl */
  `

	vec3 sampleHemisphere( vec3 n, vec2 uv ) {

		// https://www.rorydriscoll.com/2009/01/07/better-sampling/
		// https://graphics.pixar.com/library/OrthonormalB/paper.pdf
		float sign = n.z == 0.0 ? 1.0 : sign( n.z );
		float a = - 1.0 / ( sign + n.z );
		float b = n.x * n.y * a;
		vec3 b1 = vec3( 1.0 + sign * n.x * n.x * a, sign * b, - sign * n.x );
		vec3 b2 = vec3( b, sign + n.y * n.y * a, - n.y );

		float r = sqrt( uv.x );
		float theta = 2.0 * PI * uv.y;
		float x = r * cos( theta );
		float y = r * sin( theta );
		return x * b1 + y * b2 + sqrt( 1.0 - uv.x ) * n;

	}

	vec2 sampleTriangle( vec2 a, vec2 b, vec2 c, vec2 r ) {

		// get the edges of the triangle and the diagonal across the
		// center of the parallelogram
		vec2 e1 = a - b;
		vec2 e2 = c - b;
		vec2 diag = normalize( e1 + e2 );

		// pick the point in the parallelogram
		if ( r.x + r.y > 1.0 ) {

			r = vec2( 1.0 ) - r;

		}

		return e1 * r.x + e2 * r.y;

	}

	vec2 sampleCircle( vec2 uv ) {

		float angle = 2.0 * PI * uv.x;
		float radius = sqrt( uv.y );
		return vec2( cos( angle ), sin( angle ) ) * radius;

	}

	vec3 sampleSphere( vec2 uv ) {

		float u = ( uv.x - 0.5 ) * 2.0;
		float t = uv.y * PI * 2.0;
		float f = sqrt( 1.0 - u * u );

		return vec3( f * cos( t ), f * sin( t ), u );

	}

	vec2 sampleRegularPolygon( int sides, vec3 uvw ) {

		sides = max( sides, 3 );

		vec3 r = uvw;
		float anglePerSegment = 2.0 * PI / float( sides );
		float segment = floor( float( sides ) * r.x );

		float angle1 = anglePerSegment * segment;
		float angle2 = angle1 + anglePerSegment;
		vec2 a = vec2( sin( angle1 ), cos( angle1 ) );
		vec2 b = vec2( 0.0, 0.0 );
		vec2 c = vec2( sin( angle2 ), cos( angle2 ) );

		return sampleTriangle( a, b, c, r.yz );

	}

	// samples an aperture shape with the given number of sides. 0 means circle
	vec2 sampleAperture( int blades, vec3 uvw ) {

		return blades == 0 ?
			sampleCircle( uvw.xy ) :
			sampleRegularPolygon( blades, uvw );

	}


`
);

// npm/three-gpu-pathtracer/src/shader/common/fresnel_functions.glsl.js
var fresnel_functions = (
  /* glsl */
  `

	bool totalInternalReflection( float cosTheta, float eta ) {

		float sinTheta = sqrt( 1.0 - cosTheta * cosTheta );
		return eta * sinTheta > 1.0;

	}

	// https://google.github.io/filament/Filament.md.html#materialsystem/diffusebrdf
	float schlickFresnel( float cosine, float f0 ) {

		return f0 + ( 1.0 - f0 ) * pow( 1.0 - cosine, 5.0 );

	}

	vec3 schlickFresnel( float cosine, vec3 f0 ) {

		return f0 + ( 1.0 - f0 ) * pow( 1.0 - cosine, 5.0 );

	}

	vec3 schlickFresnel( float cosine, vec3 f0, vec3 f90 ) {

		return f0 + ( f90 - f0 ) * pow( 1.0 - cosine, 5.0 );

	}

	float dielectricFresnel( float cosThetaI, float eta ) {

		// https://schuttejoe.github.io/post/disneybsdf/
		float ni = eta;
		float nt = 1.0;

		// Check for total internal reflection
		float sinThetaISq = 1.0f - cosThetaI * cosThetaI;
		float sinThetaTSq = eta * eta * sinThetaISq;
		if( sinThetaTSq >= 1.0 ) {

			return 1.0;

		}

		float sinThetaT = sqrt( sinThetaTSq );

		float cosThetaT = sqrt( max( 0.0, 1.0f - sinThetaT * sinThetaT ) );
		float rParallel = ( ( nt * cosThetaI ) - ( ni * cosThetaT ) ) / ( ( nt * cosThetaI ) + ( ni * cosThetaT ) );
		float rPerpendicular = ( ( ni * cosThetaI ) - ( nt * cosThetaT ) ) / ( ( ni * cosThetaI ) + ( nt * cosThetaT ) );
		return ( rParallel * rParallel + rPerpendicular * rPerpendicular ) / 2.0;

	}

	// https://raytracing.github.io/books/RayTracingInOneWeekend.html#dielectrics/schlickapproximation
	float iorRatioToF0( float eta ) {

		return pow( ( 1.0 - eta ) / ( 1.0 + eta ), 2.0 );

	}

	vec3 evaluateFresnel( float cosTheta, float eta, vec3 f0, vec3 f90 ) {

		if ( totalInternalReflection( cosTheta, eta ) ) {

			return f90;

		}

		return schlickFresnel( cosTheta, f0, f90 );

	}

	// TODO: disney fresnel was removed and replaced with this fresnel function to better align with
	// the glTF but is causing blown out pixels. Should be revisited
	// float evaluateFresnelWeight( float cosTheta, float eta, float f0 ) {

	// 	if ( totalInternalReflection( cosTheta, eta ) ) {

	// 		return 1.0;

	// 	}

	// 	return schlickFresnel( cosTheta, f0 );

	// }

	// https://schuttejoe.github.io/post/disneybsdf/
	float disneyFresnel( vec3 wo, vec3 wi, vec3 wh, float f0, float eta, float metalness ) {

		float dotHV = dot( wo, wh );
		if ( totalInternalReflection( dotHV, eta ) ) {

			return 1.0;

		}

		float dotHL = dot( wi, wh );
		float dielectricFresnel = dielectricFresnel( abs( dotHV ), eta );
		float metallicFresnel = schlickFresnel( dotHL, f0 );

		return mix( dielectricFresnel, metallicFresnel, metalness );

	}

`
);

// npm/three-gpu-pathtracer/src/shader/common/math_functions.glsl.js
var math_functions = (
  /* glsl */
  `

	// Fast arccos approximation used to remove banding artifacts caused by numerical errors in acos.
	// This is a cubic Lagrange interpolating polynomial for x = [-1, -1/2, 0, 1/2, 1].
	// For more information see: https://github.com/gkjohnson/three-gpu-pathtracer/pull/171#issuecomment-1152275248
	float acosApprox( float x ) {

		x = clamp( x, -1.0, 1.0 );
		return ( - 0.69813170079773212 * x * x - 0.87266462599716477 ) * x + 1.5707963267948966;

	}

	// An acos with input values bound to the range [-1, 1].
	float acosSafe( float x ) {

		return acos( clamp( x, -1.0, 1.0 ) );

	}

	float saturateCos( float val ) {

		return clamp( val, 0.001, 1.0 );

	}

	float square( float t ) {

		return t * t;

	}

	vec2 square( vec2 t ) {

		return t * t;

	}

	vec3 square( vec3 t ) {

		return t * t;

	}

	vec4 square( vec4 t ) {

		return t * t;

	}

	vec2 rotateVector( vec2 v, float t ) {

		float ac = cos( t );
		float as = sin( t );
		return vec2(
			v.x * ac - v.y * as,
			v.x * as + v.y * ac
		);

	}

	// forms a basis with the normal vector as Z
	mat3 getBasisFromNormal( vec3 normal ) {

		vec3 other;
		if ( abs( normal.x ) > 0.5 ) {

			other = vec3( 0.0, 1.0, 0.0 );

		} else {

			other = vec3( 1.0, 0.0, 0.0 );

		}

		vec3 ortho = normalize( cross( normal, other ) );
		vec3 ortho2 = normalize( cross( normal, ortho ) );
		return mat3( ortho2, ortho, normal );

	}

`
);

// npm/three-gpu-pathtracer/src/shader/common/shape_intersection_functions.glsl.js
var shape_intersection_functions = (
  /* glsl */
  `

	// Finds the point where the ray intersects the plane defined by u and v and checks if this point
	// falls in the bounds of the rectangle on that same plane.
	// Plane intersection: https://lousodrome.net/blog/light/2020/07/03/intersection-of-a-ray-and-a-plane/
	bool intersectsRectangle( vec3 center, vec3 normal, vec3 u, vec3 v, vec3 rayOrigin, vec3 rayDirection, inout float dist ) {

		float t = dot( center - rayOrigin, normal ) / dot( rayDirection, normal );

		if ( t > EPSILON ) {

			vec3 p = rayOrigin + rayDirection * t;
			vec3 vi = p - center;

			// check if p falls inside the rectangle
			float a1 = dot( u, vi );
			if ( abs( a1 ) <= 0.5 ) {

				float a2 = dot( v, vi );
				if ( abs( a2 ) <= 0.5 ) {

					dist = t;
					return true;

				}

			}

		}

		return false;

	}

	// Finds the point where the ray intersects the plane defined by u and v and checks if this point
	// falls in the bounds of the circle on that same plane. See above URL for a description of the plane intersection algorithm.
	bool intersectsCircle( vec3 position, vec3 normal, vec3 u, vec3 v, vec3 rayOrigin, vec3 rayDirection, inout float dist ) {

		float t = dot( position - rayOrigin, normal ) / dot( rayDirection, normal );

		if ( t > EPSILON ) {

			vec3 hit = rayOrigin + rayDirection * t;
			vec3 vi = hit - position;

			float a1 = dot( u, vi );
			float a2 = dot( v, vi );

			if( length( vec2( a1, a2 ) ) <= 0.5 ) {

				dist = t;
				return true;

			}

		}

		return false;

	}

`
);

// npm/three-gpu-pathtracer/src/shader/common/texture_sample_functions.glsl.js
var texture_sample_functions = (
  /*glsl */
  `

	// add texel fetch functions for texture arrays
	vec4 texelFetch1D( sampler2DArray tex, int layer, uint index ) {

		uint width = uint( textureSize( tex, 0 ).x );
		uvec2 uv;
		uv.x = index % width;
		uv.y = index / width;

		return texelFetch( tex, ivec3( uv, layer ), 0 );

	}

	vec4 textureSampleBarycoord( sampler2DArray tex, int layer, vec3 barycoord, uvec3 faceIndices ) {

		return
			barycoord.x * texelFetch1D( tex, layer, faceIndices.x ) +
			barycoord.y * texelFetch1D( tex, layer, faceIndices.y ) +
			barycoord.z * texelFetch1D( tex, layer, faceIndices.z );

	}

	mat4x2 readHitUvs( sampler2DArray tex, vec3 barycoord, uvec3 indices ) {
		mat4x2 uv = mat4x2( 0.0 );
		uv[0] = textureSampleBarycoord( tex, ATTR_UV, barycoord, indices ).xy;
		#if TRACE_UV_COUNT > 1
		uv[1] = textureSampleBarycoord( tex, 4, barycoord, indices ).xy;
		#endif
		#if TRACE_UV_COUNT > 2
		uv[2] = textureSampleBarycoord( tex, 5, barycoord, indices ).xy;
		#endif
		#if TRACE_UV_COUNT > 3
		uv[3] = textureSampleBarycoord( tex, 6, barycoord, indices ).xy;
		#endif
		return uv;
	}

	vec3 transformMapUv( mat3 transform, mat4x2 uv ) {
		#if TRACE_UV_COUNT > 1
		return transform * vec3( uv[int( transform[0].z )], 1.0 );
		#else
		return transform * vec3( uv[0], 1.0 );
		#endif
	}

`
);

// npm/three-gpu-pathtracer/src/shader/common/util_functions.glsl.js
var util_functions = (
  /* glsl */
  `

	// TODO: possibly this should be renamed something related to material or path tracing logic

	#ifndef RAY_OFFSET
	#define RAY_OFFSET 1e-4
	#endif

	// adjust the hit point by the surface normal by a factor of some offset and the
	// maximum component-wise value of the current point to accommodate floating point
	// error as values increase.
	vec3 stepRayOrigin( vec3 rayOrigin, vec3 rayDirection, vec3 offset, float dist ) {

		vec3 point = rayOrigin + rayDirection * dist;
		vec3 absPoint = abs( point );
		float maxPoint = max( absPoint.x, max( absPoint.y, absPoint.z ) );
		return point + offset * ( maxPoint + 1.0 ) * RAY_OFFSET;

	}

	// https://github.com/KhronosGroup/glTF/blob/main/extensions/2.0/Khronos/KHR_materials_volume/README.md#attenuation
	vec3 transmissionAttenuation( float dist, vec3 attColor, float attDist ) {

		vec3 ot = - log( attColor ) / attDist;
		return exp( - ot * dist );

	}

	vec3 getHalfVector( vec3 wi, vec3 wo, float eta ) {

		// get the half vector - assuming if the light incident vector is on the other side
		// of the that it's transmissive.
		vec3 h;
		if ( wi.z > 0.0 ) {

			h = normalize( wi + wo );

		} else {

			// Scale by the ior ratio to retrieve the appropriate half vector
			// From Section 2.2 on computing the transmission half vector:
			// https://blog.selfshadow.com/publications/s2015-shading-course/burley/s2015_pbs_disney_bsdf_notes.pdf
			h = normalize( wi + wo * eta );

		}

		h *= sign( h.z );
		return h;

	}

	vec3 getHalfVector( vec3 a, vec3 b ) {

		return normalize( a + b );

	}

	// The discrepancy between interpolated surface normal and geometry normal can cause issues when a ray
	// is cast that is on the top side of the geometry normal plane but below the surface normal plane. If
	// we find a ray like that we ignore it to avoid artifacts.
	// This function returns if the direction is on the same side of both planes.
	bool isDirectionValid( vec3 direction, vec3 surfaceNormal, vec3 geometryNormal ) {

		bool aboveSurfaceNormal = dot( direction, surfaceNormal ) > 0.0;
		bool aboveGeometryNormal = dot( direction, geometryNormal ) > 0.0;
		return aboveSurfaceNormal == aboveGeometryNormal;

	}

	// ray sampling x and z are swapped to align with expected background view
	vec2 equirectDirectionToUv( vec3 direction ) {

		// from Spherical.setFromCartesianCoords
		vec2 uv = vec2( atan( direction.z, direction.x ), acos( direction.y ) );
		uv /= vec2( 2.0 * PI, PI );

		// apply adjustments to get values in range [0, 1] and y right side up
		uv.x += 0.5;
		uv.y = 1.0 - uv.y;
		return uv;

	}

	vec3 equirectUvToDirection( vec2 uv ) {

		// undo above adjustments
		uv.x -= 0.5;
		uv.y = 1.0 - uv.y;

		// from Vector3.setFromSphericalCoords
		float theta = uv.x * 2.0 * PI;
		float phi = uv.y * PI;

		float sinPhi = sin( phi );

		return vec3( sinPhi * cos( theta ), cos( phi ), sinPhi * sin( theta ) );

	}

	// power heuristic for multiple importance sampling
	float misHeuristic( float a, float b ) {

		float aa = a * a;
		float bb = b * b;
		return aa / ( aa + bb );

	}

	// tentFilter from Peter Shirley's 'Realistic Ray Tracing (2nd Edition)' book, pg. 60
	// erichlof/THREE.js-PathTracing-Renderer/
	float tentFilter( float x ) {

		return x < 0.5 ? sqrt( 2.0 * x ) - 1.0 : 1.0 - sqrt( 2.0 - ( 2.0 * x ) );

	}
`
);

// npm/three-gpu-pathtracer/src/shader/rand/pcg.glsl.js
var pcg_functions = (
  /* glsl */
  `

	// https://www.shadertoy.com/view/wltcRS
	uvec4 WHITE_NOISE_SEED;

	void rng_initialize( vec2 p, int frame ) {

		// white noise seed
		WHITE_NOISE_SEED = uvec4( p, uint( frame ), uint( p.x ) + uint( p.y ) );

	}

	// https://www.pcg-random.org/
	void pcg4d( inout uvec4 v ) {

		v = v * 1664525u + 1013904223u;
		v.x += v.y * v.w;
		v.y += v.z * v.x;
		v.z += v.x * v.y;
		v.w += v.y * v.z;
		v = v ^ ( v >> 16u );
		v.x += v.y*v.w;
		v.y += v.z*v.x;
		v.z += v.x*v.y;
		v.w += v.y*v.z;

	}

	// returns [ 0, 1 ]
	float pcgRand() {

		pcg4d( WHITE_NOISE_SEED );
		return float( WHITE_NOISE_SEED.x ) / float( 0xffffffffu );

	}

	vec2 pcgRand2() {

		pcg4d( WHITE_NOISE_SEED );
		return vec2( WHITE_NOISE_SEED.xy ) / float(0xffffffffu);

	}

	vec3 pcgRand3() {

		pcg4d( WHITE_NOISE_SEED );
		return vec3( WHITE_NOISE_SEED.xyz ) / float( 0xffffffffu );

	}

	vec4 pcgRand4() {

		pcg4d( WHITE_NOISE_SEED );
		return vec4( WHITE_NOISE_SEED ) / float( 0xffffffffu );

	}
`
);

// npm/three-gpu-pathtracer/src/shader/rand/stratified.glsl.js
var stratified_functions = (
  /* glsl */
  `

	uniform sampler2D stratifiedTexture;
	uniform sampler2D stratifiedOffsetTexture;

	uint sobolPixelIndex = 0u;
	uint sobolPathIndex = 0u;
	uint sobolBounceIndex = 0u;
	vec4 pixelSeed = vec4( 0 );

	vec4 rand4( int v ) {

		ivec2 uv = ivec2( v, sobolBounceIndex );
		vec4 stratifiedSample = texelFetch( stratifiedTexture, uv, 0 );
		return fract( stratifiedSample + pixelSeed.r ); // blue noise + stratified samples

	}

	vec3 rand3( int v ) {

		return rand4( v ).xyz;

	}

	vec2 rand2( int v ) {

		return rand4( v ).xy;

	}

	float rand( int v ) {

		return rand4( v ).x;

	}

	void rng_initialize( vec2 screenCoord, int frame ) {

		// tile the small noise texture across the entire screen
		ivec2 noiseSize = ivec2( textureSize( stratifiedOffsetTexture, 0 ) );
		ivec2 pixel = ivec2( screenCoord.xy ) % noiseSize;
		vec2 pixelWidth = 1.0 / vec2( noiseSize );
		vec2 uv = vec2( pixel ) * pixelWidth + pixelWidth * 0.5;

		// note that using "texelFetch" here seems to break Android for some reason
		pixelSeed = texture( stratifiedOffsetTexture, uv );

	}

`
);

// npm/three-gpu-pathtracer/src/shader/bsdf/bsdf_functions.glsl.js
var bsdf_functions = (
  /* glsl */
  `

	// diffuse
	float diffuseEval( vec3 wo, vec3 wi, vec3 wh, SurfaceRecord surf, inout vec3 color ) {

		// https://schuttejoe.github.io/post/disneybsdf/
		float fl = schlickFresnel( wi.z, 0.0 );
		float fv = schlickFresnel( wo.z, 0.0 );

		float metalFactor = ( 1.0 - surf.metalness );
		float transFactor = ( 1.0 - surf.transmission );
		float rr = 0.5 + 2.0 * surf.roughness * fl * fl;
		float retro = rr * ( fl + fv + fl * fv * ( rr - 1.0f ) );
		float lambert = ( 1.0f - 0.5f * fl ) * ( 1.0f - 0.5f * fv );

		// TODO: subsurface approx?

		// float F = evaluateFresnelWeight( dot( wo, wh ), surf.eta, surf.f0 );
		float F = disneyFresnel( wo, wi, wh, surf.f0, surf.eta, surf.metalness );
		color = ( 1.0 - F ) * transFactor * metalFactor * wi.z * surf.color * ( retro + lambert ) / PI;

		return wi.z / PI;

	}

	vec3 diffuseDirection( vec3 wo, SurfaceRecord surf ) {

		vec3 lightDirection = sampleSphere( rand2( 11 ) );
		lightDirection.z += 1.0;
		lightDirection = normalize( lightDirection );

		return lightDirection;

	}

	// specular
	float specularEval( vec3 wo, vec3 wi, vec3 wh, SurfaceRecord surf, inout vec3 color ) {

		// if roughness is set to 0 then D === NaN which results in black pixels
		float metalness = surf.metalness;
		float roughness = surf.filteredRoughness;

		float eta = surf.eta;
		float f0 = surf.f0;

		vec3 f0Color = mix( f0 * surf.specularColor * surf.specularIntensity, surf.color, surf.metalness );
		vec3 f90Color = vec3( mix( surf.specularIntensity, 1.0, surf.metalness ) );
		vec3 F = evaluateFresnel( dot( wo, wh ), eta, f0Color, f90Color );

		vec3 iridescenceF = evalIridescence( 1.0, surf.iridescenceIor, dot( wi, wh ), surf.iridescenceThickness, f0Color );
		F = mix( F, iridescenceF,  surf.iridescence );

		// PDF
		// See 14.1.1 Microfacet BxDFs in https://www.pbr-book.org/
		float incidentTheta = acos( wo.z );
		float G = ggxShadowMaskG2( wi, wo, roughness );
		float D = ggxDistribution( wh, roughness );
		float G1 = ggxShadowMaskG1( incidentTheta, roughness );
		float ggxPdf = D * G1 * max( 0.0, abs( dot( wo, wh ) ) ) / abs ( wo.z );

		color = wi.z * F * G * D / ( 4.0 * abs( wi.z * wo.z ) );
		return ggxPdf / ( 4.0 * dot( wo, wh ) );

	}

	vec3 specularDirection( vec3 wo, SurfaceRecord surf ) {

		// sample ggx vndf distribution which gives a new normal
		float roughness = surf.filteredRoughness;
		vec3 halfVector = ggxDirection(
			wo,
			vec2( roughness ),
			rand2( 12 )
		);

		// apply to new ray by reflecting off the new normal
		return - reflect( wo, halfVector );

	}


	// transmission
	/*
	float transmissionEval( vec3 wo, vec3 wi, vec3 wh, SurfaceRecord surf, inout vec3 color ) {

		// See section 4.2 in https://www.cs.cornell.edu/~srm/publications/EGSR07-btdf.pdf

		float filteredRoughness = surf.filteredRoughness;
		float eta = surf.eta;
		bool frontFace = surf.frontFace;
		bool thinFilm = surf.thinFilm;

		color = surf.transmission * surf.color;

		float denom = pow( eta * dot( wi, wh ) + dot( wo, wh ), 2.0 );
		return ggxPDF( wo, wh, filteredRoughness ) / denom;

	}

	vec3 transmissionDirection( vec3 wo, SurfaceRecord surf ) {

		float filteredRoughness = surf.filteredRoughness;
		float eta = surf.eta;
		bool frontFace = surf.frontFace;

		// sample ggx vndf distribution which gives a new normal
		vec3 halfVector = ggxDirection(
			wo,
			vec2( filteredRoughness ),
			rand2( 13 )
		);

		vec3 lightDirection = refract( normalize( - wo ), halfVector, eta );
		if ( surf.thinFilm ) {

			lightDirection = - refract( normalize( - lightDirection ), - vec3( 0.0, 0.0, 1.0 ), 1.0 / eta );

		}

		return normalize( lightDirection );

	}
	*/

	// TODO: This is just using a basic cosine-weighted specular distribution with an
	// incorrect PDF value at the moment. Update it to correctly use a GGX distribution
	float transmissionEval( vec3 wo, vec3 wi, vec3 wh, SurfaceRecord surf, inout vec3 color ) {

		color = surf.transmission * surf.color;

		// PDF
		// float F = evaluateFresnelWeight( dot( wo, wh ), surf.eta, surf.f0 );
		// float F = disneyFresnel( wo, wi, wh, surf.f0, surf.eta, surf.metalness );
		// if ( F >= 1.0 ) {

		// 	return 0.0;

		// }

		// return 1.0 / ( 1.0 - F );

		// reverted to previous to transmission. The above was causing black pixels
		float eta = surf.eta;
		float f0 = surf.f0;
		float cosTheta = min( wo.z, 1.0 );
		float sinTheta = sqrt( 1.0 - cosTheta * cosTheta );
		float reflectance = schlickFresnel( cosTheta, f0 );
		bool cannotRefract = eta * sinTheta > 1.0;
		if ( cannotRefract ) {

			return 0.0;

		}

		return 1.0 / ( 1.0 - reflectance );

	}

	vec3 transmissionDirection( vec3 wo, SurfaceRecord surf ) {

		float roughness = surf.filteredRoughness;
		float eta = surf.eta;
		vec3 halfVector = normalize( vec3( 0.0, 0.0, 1.0 ) + sampleSphere( rand2( 13 ) ) * roughness );
		vec3 lightDirection = refract( normalize( - wo ), halfVector, eta );

		if ( surf.thinFilm ) {

			lightDirection = - refract( normalize( - lightDirection ), - vec3( 0.0, 0.0, 1.0 ), 1.0 / eta );

		}
		return normalize( lightDirection );

	}

	// clearcoat
	float clearcoatEval( vec3 wo, vec3 wi, vec3 wh, SurfaceRecord surf, inout vec3 color ) {

		float ior = 1.5;
		float f0 = iorRatioToF0( ior );
		bool frontFace = surf.frontFace;
		float roughness = surf.filteredClearcoatRoughness;

		float eta = frontFace ? 1.0 / ior : ior;
		float G = ggxShadowMaskG2( wi, wo, roughness );
		float D = ggxDistribution( wh, roughness );
		float F = schlickFresnel( dot( wi, wh ), f0 );

		float fClearcoat = F * D * G / ( 4.0 * abs( wi.z * wo.z ) );
		color = color * ( 1.0 - surf.clearcoat * F ) + fClearcoat * surf.clearcoat * wi.z;

		// PDF
		// See equation (27) in http://jcgt.org/published/0003/02/03/
		return ggxPDF( wo, wh, roughness ) / ( 4.0 * dot( wi, wh ) );

	}

	vec3 clearcoatDirection( vec3 wo, SurfaceRecord surf ) {

		// sample ggx vndf distribution which gives a new normal
		float roughness = surf.filteredClearcoatRoughness;
		vec3 halfVector = ggxDirection(
			wo,
			vec2( roughness ),
			rand2( 14 )
		);

		// apply to new ray by reflecting off the new normal
		return - reflect( wo, halfVector );

	}

	// sheen
	vec3 sheenColor( vec3 wo, vec3 wi, vec3 wh, SurfaceRecord surf ) {

		float cosThetaO = saturateCos( wo.z );
		float cosThetaI = saturateCos( wi.z );
		float cosThetaH = wh.z;

		float D = velvetD( cosThetaH, surf.sheenRoughness );
		float G = velvetG( cosThetaO, cosThetaI, surf.sheenRoughness );

		// See equation (1) in http://www.aconty.com/pdf/s2017_pbs_imageworks_sheen.pdf
		vec3 color = surf.sheenColor;
		color *= D * G / ( 4.0 * abs( cosThetaO * cosThetaI ) );

		return color;

	}

	// bsdf
	void getLobeWeights(
		vec3 wo, vec3 wi, vec3 wh, vec3 clearcoatWo, SurfaceRecord surf,
		inout float diffuseWeight, inout float specularWeight, inout float transmissionWeight, inout float clearcoatWeight
	) {

		float metalness = surf.metalness;
		float transmission = surf.transmission;
		// float fEstimate = evaluateFresnelWeight( dot( wo, wh ), surf.eta, surf.f0 );
		float fEstimate = disneyFresnel( wo, wi, wh, surf.f0, surf.eta, surf.metalness );

		float transSpecularProb = mix( max( 0.25, fEstimate ), 1.0, metalness );
		float diffSpecularProb = 0.5 + 0.5 * metalness;

		diffuseWeight = ( 1.0 - transmission ) * ( 1.0 - diffSpecularProb );
		specularWeight = transmission * transSpecularProb + ( 1.0 - transmission ) * diffSpecularProb;
		transmissionWeight = transmission * ( 1.0 - transSpecularProb );
		clearcoatWeight = surf.clearcoat * schlickFresnel( clearcoatWo.z, 0.04 );

		float totalWeight = diffuseWeight + specularWeight + transmissionWeight + clearcoatWeight;
		diffuseWeight /= totalWeight;
		specularWeight /= totalWeight;
		transmissionWeight /= totalWeight;
		clearcoatWeight /= totalWeight;
	}

	float bsdfEval(
		vec3 wo, vec3 clearcoatWo, vec3 wi, vec3 clearcoatWi, SurfaceRecord surf,
		float diffuseWeight, float specularWeight, float transmissionWeight, float clearcoatWeight, inout float specularPdf, inout vec3 color
	) {

		float metalness = surf.metalness;
		float transmission = surf.transmission;

		float spdf = 0.0;
		float dpdf = 0.0;
		float tpdf = 0.0;
		float cpdf = 0.0;
		color = vec3( 0.0 );

		vec3 halfVector = getHalfVector( wi, wo, surf.eta );

		// diffuse
		if ( diffuseWeight > 0.0 && wi.z > 0.0 ) {

			dpdf = diffuseEval( wo, wi, halfVector, surf, color );
			color *= 1.0 - surf.transmission;

		}

		// ggx specular
		if ( specularWeight > 0.0 && wi.z > 0.0 ) {

			vec3 outColor;
			spdf = specularEval( wo, wi, getHalfVector( wi, wo ), surf, outColor );
			color += outColor;

		}

		// transmission
		if ( transmissionWeight > 0.0 && wi.z < 0.0 ) {

			tpdf = transmissionEval( wo, wi, halfVector, surf, color );

		}

		// sheen
		color *= mix( 1.0, sheenAlbedoScaling( wo, wi, surf ), surf.sheen );
		color += sheenColor( wo, wi, halfVector, surf ) * surf.sheen;

		// clearcoat
		if ( clearcoatWi.z >= 0.0 && clearcoatWeight > 0.0 ) {

			vec3 clearcoatHalfVector = getHalfVector( clearcoatWo, clearcoatWi );
			cpdf = clearcoatEval( clearcoatWo, clearcoatWi, clearcoatHalfVector, surf, color );

		}

		float pdf =
			dpdf * diffuseWeight
			+ spdf * specularWeight
			+ tpdf * transmissionWeight
			+ cpdf * clearcoatWeight;

		// retrieve specular rays for the shadows flag
		specularPdf = spdf * specularWeight + cpdf * clearcoatWeight;

		return pdf;

	}

	float bsdfResult( vec3 worldWo, vec3 worldWi, SurfaceRecord surf, inout vec3 color ) {

		if ( surf.volumeParticle ) {

			color = surf.color / ( 4.0 * PI );
			return 1.0 / ( 4.0 * PI );

		}

		vec3 wo = normalize( surf.normalInvBasis * worldWo );
		vec3 wi = normalize( surf.normalInvBasis * worldWi );

		vec3 clearcoatWo = normalize( surf.clearcoatInvBasis * worldWo );
		vec3 clearcoatWi = normalize( surf.clearcoatInvBasis * worldWi );

		vec3 wh = getHalfVector( wo, wi, surf.eta );
		float diffuseWeight;
		float specularWeight;
		float transmissionWeight;
		float clearcoatWeight;
		getLobeWeights( wo, wi, wh, clearcoatWo, surf, diffuseWeight, specularWeight, transmissionWeight, clearcoatWeight );

		float specularPdf;
		return bsdfEval( wo, clearcoatWo, wi, clearcoatWi, surf, diffuseWeight, specularWeight, transmissionWeight, clearcoatWeight, specularPdf, color );

	}

	ScatterRecord bsdfSample( vec3 worldWo, SurfaceRecord surf ) {

		if ( surf.volumeParticle ) {

			ScatterRecord sampleRec;
			sampleRec.specularPdf = 0.0;
			sampleRec.pdf = 1.0 / ( 4.0 * PI );
			sampleRec.direction = sampleSphere( rand2( 16 ) );
			sampleRec.color = surf.color / ( 4.0 * PI );
			return sampleRec;

		}

		vec3 wo = normalize( surf.normalInvBasis * worldWo );
		vec3 clearcoatWo = normalize( surf.clearcoatInvBasis * worldWo );
		mat3 normalBasis = surf.normalBasis;
		mat3 invBasis = surf.normalInvBasis;
		mat3 clearcoatNormalBasis = surf.clearcoatBasis;
		mat3 clearcoatInvBasis = surf.clearcoatInvBasis;

		float diffuseWeight;
		float specularWeight;
		float transmissionWeight;
		float clearcoatWeight;
		// using normal and basically-reflected ray since we don't have proper half vector here
		getLobeWeights( wo, wo, vec3( 0, 0, 1 ), clearcoatWo, surf, diffuseWeight, specularWeight, transmissionWeight, clearcoatWeight );

		float pdf[4];
		pdf[0] = diffuseWeight;
		pdf[1] = specularWeight;
		pdf[2] = transmissionWeight;
		pdf[3] = clearcoatWeight;

		float cdf[4];
		cdf[0] = pdf[0];
		cdf[1] = pdf[1] + cdf[0];
		cdf[2] = pdf[2] + cdf[1];
		cdf[3] = pdf[3] + cdf[2];

		if( cdf[3] != 0.0 ) {

			float invMaxCdf = 1.0 / cdf[3];
			cdf[0] *= invMaxCdf;
			cdf[1] *= invMaxCdf;
			cdf[2] *= invMaxCdf;
			cdf[3] *= invMaxCdf;

		} else {

			cdf[0] = 1.0;
			cdf[1] = 0.0;
			cdf[2] = 0.0;
			cdf[3] = 0.0;

		}

		vec3 wi;
		vec3 clearcoatWi;

		float r = rand( 15 );
		if ( r <= cdf[0] ) { // diffuse

			wi = diffuseDirection( wo, surf );
			clearcoatWi = normalize( clearcoatInvBasis * normalize( normalBasis * wi ) );

		} else if ( r <= cdf[1] ) { // specular

			wi = specularDirection( wo, surf );
			clearcoatWi = normalize( clearcoatInvBasis * normalize( normalBasis * wi ) );

		} else if ( r <= cdf[2] ) { // transmission / refraction

			wi = transmissionDirection( wo, surf );
			clearcoatWi = normalize( clearcoatInvBasis * normalize( normalBasis * wi ) );

		} else if ( r <= cdf[3] ) { // clearcoat

			clearcoatWi = clearcoatDirection( clearcoatWo, surf );
			wi = normalize( invBasis * normalize( clearcoatNormalBasis * clearcoatWi ) );

		}

		ScatterRecord result;
		result.pdf = bsdfEval( wo, clearcoatWo, wi, clearcoatWi, surf, diffuseWeight, specularWeight, transmissionWeight, clearcoatWeight, result.specularPdf, result.color );
		result.direction = normalize( surf.normalBasis * wi );

		return result;

	}

`
);

// npm/three-gpu-pathtracer/src/shader/bsdf/fog_functions.glsl.js
var fog_functions = (
  /* glsl */
  `

	// returns the hit distance given the material density
	float intersectFogVolume( Material material, float u ) {

		// https://raytracing.github.io/books/RayTracingTheNextWeek.html#volumes/constantdensitymediums
		return material.opacity == 0.0 ? INFINITY : ( - 1.0 / material.opacity ) * log( u );

	}

	ScatterRecord sampleFogVolume( SurfaceRecord surf, vec2 uv ) {

		ScatterRecord sampleRec;
		sampleRec.specularPdf = 0.0;
		sampleRec.pdf = 1.0 / ( 2.0 * PI );
		sampleRec.direction = sampleSphere( uv );
		sampleRec.color = surf.color;
		return sampleRec;

	}

`
);

// npm/three-gpu-pathtracer/src/shader/bsdf/ggx_functions.glsl.js
var ggx_functions = (
  /* glsl */
  `

	// The GGX functions provide sampling and distribution information for normals as output so
	// in order to get probability of scatter direction the half vector must be computed and provided.
	// [0] https://www.cs.cornell.edu/~srm/publications/EGSR07-btdf.pdf
	// [1] https://hal.archives-ouvertes.fr/hal-01509746/document
	// [2] http://jcgt.org/published/0007/04/01/
	// [4] http://jcgt.org/published/0003/02/03/

	// trowbridge-reitz === GGX === GTR

	vec3 ggxDirection( vec3 incidentDir, vec2 roughness, vec2 uv ) {

		// TODO: try GGXVNDF implementation from reference [2], here. Needs to update ggxDistribution
		// function below, as well

		// Implementation from reference [1]
		// stretch view
		vec3 V = normalize( vec3( roughness * incidentDir.xy, incidentDir.z ) );

		// orthonormal basis
		vec3 T1 = ( V.z < 0.9999 ) ? normalize( cross( V, vec3( 0.0, 0.0, 1.0 ) ) ) : vec3( 1.0, 0.0, 0.0 );
		vec3 T2 = cross( T1, V );

		// sample point with polar coordinates (r, phi)
		float a = 1.0 / ( 1.0 + V.z );
		float r = sqrt( uv.x );
		float phi = ( uv.y < a ) ? uv.y / a * PI : PI + ( uv.y - a ) / ( 1.0 - a ) * PI;
		float P1 = r * cos( phi );
		float P2 = r * sin( phi ) * ( ( uv.y < a ) ? 1.0 : V.z );

		// compute normal
		vec3 N = P1 * T1 + P2 * T2 + V * sqrt( max( 0.0, 1.0 - P1 * P1 - P2 * P2 ) );

		// unstretch
		N = normalize( vec3( roughness * N.xy, max( 0.0, N.z ) ) );

		return N;

	}

	// Below are PDF and related functions for use in a Monte Carlo path tracer
	// as specified in Appendix B of the following paper
	// See equation (34) from reference [0]
	float ggxLamda( float theta, float roughness ) {

		float tanTheta = tan( theta );
		float tanTheta2 = tanTheta * tanTheta;
		float alpha2 = roughness * roughness;

		float numerator = - 1.0 + sqrt( 1.0 + alpha2 * tanTheta2 );
		return numerator / 2.0;

	}

	// See equation (34) from reference [0]
	float ggxShadowMaskG1( float theta, float roughness ) {

		return 1.0 / ( 1.0 + ggxLamda( theta, roughness ) );

	}

	// See equation (125) from reference [4]
	float ggxShadowMaskG2( vec3 wi, vec3 wo, float roughness ) {

		float incidentTheta = acos( wi.z );
		float scatterTheta = acos( wo.z );
		return 1.0 / ( 1.0 + ggxLamda( incidentTheta, roughness ) + ggxLamda( scatterTheta, roughness ) );

	}

	// See equation (33) from reference [0]
	float ggxDistribution( vec3 halfVector, float roughness ) {

		float a2 = roughness * roughness;
		a2 = max( EPSILON, a2 );
		float cosTheta = halfVector.z;
		float cosTheta4 = pow( cosTheta, 4.0 );

		if ( cosTheta == 0.0 ) return 0.0;

		float theta = acosSafe( halfVector.z );
		float tanTheta = tan( theta );
		float tanTheta2 = pow( tanTheta, 2.0 );

		float denom = PI * cosTheta4 * pow( a2 + tanTheta2, 2.0 );
		return ( a2 / denom );

	}

	// See equation (3) from reference [2]
	float ggxPDF( vec3 wi, vec3 halfVector, float roughness ) {

		float incidentTheta = acos( wi.z );
		float D = ggxDistribution( halfVector, roughness );
		float G1 = ggxShadowMaskG1( incidentTheta, roughness );

		return D * G1 * max( 0.0, dot( wi, halfVector ) ) / wi.z;

	}

`
);

// npm/three-gpu-pathtracer/src/shader/bsdf/iridescence_functions.glsl.js
var iridescence_functions = (
  /* glsl */
  `

	// XYZ to sRGB color space
	const mat3 XYZ_TO_REC709 = mat3(
		3.2404542, -0.9692660,  0.0556434,
		-1.5371385,  1.8760108, -0.2040259,
		-0.4985314,  0.0415560,  1.0572252
	);

	vec3 fresnel0ToIor( vec3 fresnel0 ) {

		vec3 sqrtF0 = sqrt( fresnel0 );
		return ( vec3( 1.0 ) + sqrtF0 ) / ( vec3( 1.0 ) - sqrtF0 );

	}

	// Conversion FO/IOR
	vec3 iorToFresnel0( vec3 transmittedIor, float incidentIor ) {

		return square( ( transmittedIor - vec3( incidentIor ) ) / ( transmittedIor + vec3( incidentIor ) ) );

	}

	// ior is a value between 1.0 and 3.0. 1.0 is air interface
	float iorToFresnel0( float transmittedIor, float incidentIor ) {

		return square( ( transmittedIor - incidentIor ) / ( transmittedIor + incidentIor ) );

	}

	// Fresnel equations for dielectric/dielectric interfaces. See https://belcour.github.io/blog/research/2017/05/01/brdf-thin-film.html
	vec3 evalSensitivity( float OPD, vec3 shift ) {

		float phase = 2.0 * PI * OPD * 1.0e-9;

		vec3 val = vec3( 5.4856e-13, 4.4201e-13, 5.2481e-13 );
		vec3 pos = vec3( 1.6810e+06, 1.7953e+06, 2.2084e+06 );
		vec3 var = vec3( 4.3278e+09, 9.3046e+09, 6.6121e+09 );

		vec3 xyz = val * sqrt( 2.0 * PI * var ) * cos( pos * phase + shift ) * exp( - square( phase ) * var );
		xyz.x += 9.7470e-14 * sqrt( 2.0 * PI * 4.5282e+09 ) * cos( 2.2399e+06 * phase + shift[ 0 ] ) * exp( - 4.5282e+09 * square( phase ) );
		xyz /= 1.0685e-7;

		vec3 srgb = XYZ_TO_REC709 * xyz;
		return srgb;

	}

	// See Section 4. Analytic Spectral Integration, A Practical Extension to Microfacet Theory for the Modeling of Varying Iridescence, https://hal.archives-ouvertes.fr/hal-01518344/document
	vec3 evalIridescence( float outsideIOR, float eta2, float cosTheta1, float thinFilmThickness, vec3 baseF0 ) {

		vec3 I;

		// Force iridescenceIor -> outsideIOR when thinFilmThickness -> 0.0
		float iridescenceIor = mix( outsideIOR, eta2, smoothstep( 0.0, 0.03, thinFilmThickness ) );

		// Evaluate the cosTheta on the base layer (Snell law)
		float sinTheta2Sq = square( outsideIOR / iridescenceIor ) * ( 1.0 - square( cosTheta1 ) );

		// Handle TIR:
		float cosTheta2Sq = 1.0 - sinTheta2Sq;
		if ( cosTheta2Sq < 0.0 ) {

			return vec3( 1.0 );

		}

		float cosTheta2 = sqrt( cosTheta2Sq );

		// First interface
		float R0 = iorToFresnel0( iridescenceIor, outsideIOR );
		float R12 = schlickFresnel( cosTheta1, R0 );
		float R21 = R12;
		float T121 = 1.0 - R12;
		float phi12 = 0.0;
		if ( iridescenceIor < outsideIOR ) {

			phi12 = PI;

		}

		float phi21 = PI - phi12;

		// Second interface
		vec3 baseIOR = fresnel0ToIor( clamp( baseF0, 0.0, 0.9999 ) ); // guard against 1.0
		vec3 R1 = iorToFresnel0( baseIOR, iridescenceIor );
		vec3 R23 = schlickFresnel( cosTheta2, R1 );
		vec3 phi23 = vec3( 0.0 );
		if ( baseIOR[0] < iridescenceIor ) {

			phi23[ 0 ] = PI;

		}

		if ( baseIOR[1] < iridescenceIor ) {

			phi23[ 1 ] = PI;

		}

		if ( baseIOR[2] < iridescenceIor ) {

			phi23[ 2 ] = PI;

		}

		// Phase shift
		float OPD = 2.0 * iridescenceIor * thinFilmThickness * cosTheta2;
		vec3 phi = vec3( phi21 ) + phi23;

		// Compound terms
		vec3 R123 = clamp( R12 * R23, 1e-5, 0.9999 );
		vec3 r123 = sqrt( R123 );
		vec3 Rs = square( T121 ) * R23 / ( vec3( 1.0 ) - R123 );

		// Reflectance term for m = 0 (DC term amplitude)
		vec3 C0 = R12 + Rs;
		I = C0;

		// Reflectance term for m > 0 (pairs of diracs)
		vec3 Cm = Rs - T121;
		for ( int m = 1; m <= 2; ++ m ) {

			Cm *= r123;
			vec3 Sm = 2.0 * evalSensitivity( float( m ) * OPD, float( m ) * phi );
			I += Cm * Sm;

		}

		// Since out of gamut colors might be produced, negative color values are clamped to 0.
		return max( I, vec3( 0.0 ) );

	}

`
);

// npm/three-gpu-pathtracer/src/shader/bsdf/sheen_functions.glsl.js
var sheen_functions = (
  /* glsl */
  `

	// See equation (2) in http://www.aconty.com/pdf/s2017_pbs_imageworks_sheen.pdf
	float velvetD( float cosThetaH, float roughness ) {

		float alpha = max( roughness, 0.07 );
		alpha = alpha * alpha;

		float invAlpha = 1.0 / alpha;

		float sqrCosThetaH = cosThetaH * cosThetaH;
		float sinThetaH = max( 1.0 - sqrCosThetaH, 0.001 );

		return ( 2.0 + invAlpha ) * pow( sinThetaH, 0.5 * invAlpha ) / ( 2.0 * PI );

	}

	float velvetParamsInterpolate( int i, float oneMinusAlphaSquared ) {

		const float p0[5] = float[5]( 25.3245, 3.32435, 0.16801, -1.27393, -4.85967 );
		const float p1[5] = float[5]( 21.5473, 3.82987, 0.19823, -1.97760, -4.32054 );

		return mix( p1[i], p0[i], oneMinusAlphaSquared );

	}

	float velvetL( float x, float alpha ) {

		float oneMinusAlpha = 1.0 - alpha;
		float oneMinusAlphaSquared = oneMinusAlpha * oneMinusAlpha;

		float a = velvetParamsInterpolate( 0, oneMinusAlphaSquared );
		float b = velvetParamsInterpolate( 1, oneMinusAlphaSquared );
		float c = velvetParamsInterpolate( 2, oneMinusAlphaSquared );
		float d = velvetParamsInterpolate( 3, oneMinusAlphaSquared );
		float e = velvetParamsInterpolate( 4, oneMinusAlphaSquared );

		return a / ( 1.0 + b * pow( abs( x ), c ) ) + d * x + e;

	}

	// See equation (3) in http://www.aconty.com/pdf/s2017_pbs_imageworks_sheen.pdf
	float velvetLambda( float cosTheta, float alpha ) {

		return abs( cosTheta ) < 0.5 ? exp( velvetL( cosTheta, alpha ) ) : exp( 2.0 * velvetL( 0.5, alpha ) - velvetL( 1.0 - cosTheta, alpha ) );

	}

	// See Section 3, Shadowing Term, in http://www.aconty.com/pdf/s2017_pbs_imageworks_sheen.pdf
	float velvetG( float cosThetaO, float cosThetaI, float roughness ) {

		float alpha = max( roughness, 0.07 );
		alpha = alpha * alpha;

		return 1.0 / ( 1.0 + velvetLambda( cosThetaO, alpha ) + velvetLambda( cosThetaI, alpha ) );

	}

	float directionalAlbedoSheen( float cosTheta, float alpha ) {

		cosTheta = saturate( cosTheta );

		float c = 1.0 - cosTheta;
		float c3 = c * c * c;

		return 0.65584461 * c3 + 1.0 / ( 4.16526551 + exp( -7.97291361 * sqrt( alpha ) + 6.33516894 ) );

	}

	float sheenAlbedoScaling( vec3 wo, vec3 wi, SurfaceRecord surf ) {

		float alpha = max( surf.sheenRoughness, 0.07 );
		alpha = alpha * alpha;

		float maxSheenColor = max( max( surf.sheenColor.r, surf.sheenColor.g ), surf.sheenColor.b );

		float eWo = directionalAlbedoSheen( saturateCos( wo.z ), alpha );
		float eWi = directionalAlbedoSheen( saturateCos( wi.z ), alpha );

		return min( 1.0 - maxSheenColor * eWo, 1.0 - maxSheenColor * eWi );

	}

	// See Section 5, Layering, in http://www.aconty.com/pdf/s2017_pbs_imageworks_sheen.pdf
	float sheenAlbedoScaling( vec3 wo, SurfaceRecord surf ) {

		float alpha = max( surf.sheenRoughness, 0.07 );
		alpha = alpha * alpha;

		float maxSheenColor = max( max( surf.sheenColor.r, surf.sheenColor.g ), surf.sheenColor.b );

		float eWo = directionalAlbedoSheen( saturateCos( wo.z ), alpha );

		return 1.0 - maxSheenColor * eWo;

	}

`
);

// npm/three-gpu-pathtracer/src/shader/bvh/inside_fog_volume_function.glsl.js
var inside_fog_volume_function = (
  /* glsl */
  `

#ifndef FOG_CHECK_ITERATIONS
#define FOG_CHECK_ITERATIONS 30
#endif

// returns whether the given material is a fog material or not
bool isMaterialFogVolume( sampler2D materials, uint materialIndex ) {

	uint i = materialIndex * uint( MATERIAL_PIXELS );
	vec4 s14 = texelFetch1D( materials, i + 14u );
	return bool( int( s14.b ) & 4 );

}

// returns true if we're within the first fog volume we hit
bool bvhIntersectFogVolumeHit(
	vec3 rayOrigin, vec3 rayDirection,
	usampler2D materialIndexAttribute, sampler2D materials,
	inout Material material
) {

	material.fogVolume = false;

	for ( int i = 0; i < FOG_CHECK_ITERATIONS; i ++ ) {

		// find nearest hit
		uvec4 faceIndices = uvec4( 0u );
		vec3 faceNormal = vec3( 0.0, 0.0, 1.0 );
		vec3 barycoord = vec3( 0.0 );
		float side = 1.0;
		float dist = 0.0;
		bool hit = bvhIntersectFirstHit( bvh, rayOrigin, rayDirection, faceIndices, faceNormal, barycoord, side, dist );
		if ( hit ) {

			// if it's a fog volume return whether we hit the front or back face
			uint materialIndex = uTexelFetch1D( materialIndexAttribute, faceIndices.x ).r;
			if ( isMaterialFogVolume( materials, materialIndex ) ) {

				material = readMaterialInfo( materials, materialIndex );
				return side == - 1.0;

			} else {

				// move the ray forward
				rayOrigin = stepRayOrigin( rayOrigin, rayDirection, - faceNormal, dist );

			}

		} else {

			return false;

		}

	}

	return false;

}

`
);

// npm/three-gpu-pathtracer/src/materials/pathtracing/glsl/attenuate_hit_function.glsl.js
var attenuate_hit_function = (
  /* glsl */
  `

	// step through multiple surface hits and accumulate color attenuation based on transmissive surfaces
	// returns true if a solid surface was hit
	bool attenuateHit(
		RenderState state,
		Ray ray, float rayDist,
		out vec3 color
	) {

		// store the original bounce index so we can reset it after
		uint originalBounceIndex = sobolBounceIndex;

		int traversals = state.traversals;
		int transmissiveTraversals = state.transmissiveTraversals;
		bool isShadowRay = state.isShadowRay;
		Material fogMaterial = state.fogMaterial;

		vec3 startPoint = ray.origin;

		// hit results
		SurfaceHit surfaceHit;

		color = vec3( 1.0 );

		bool result = true;
		for ( int i = 0; i < traversals; i ++ ) {

			sobolBounceIndex ++;

			int hitType = traceScene( ray, fogMaterial, surfaceHit );

			if ( hitType == FOG_HIT ) {

				result = true;
				break;

			} else if ( hitType == SURFACE_HIT ) {

				float totalDist = distance( startPoint, ray.origin + ray.direction * surfaceHit.dist );
				if ( totalDist > rayDist ) {

					result = false;
					break;

				}

				// TODO: attenuate the contribution based on the PDF of the resulting ray including refraction values
				// Should be able to work using the material BSDF functions which will take into account specularity, etc.
				// TODO: should we account for emissive surfaces here?

				uint materialIndex = uTexelFetch1D( materialIndexAttribute, surfaceHit.faceIndices.x ).r;
				Material material = readMaterialInfo( materials, materialIndex );

				// adjust the ray to the new surface
				bool isEntering = surfaceHit.side == 1.0;
				ray.origin = stepRayOrigin( ray.origin, ray.direction, - surfaceHit.faceNormal, surfaceHit.dist );

				#if FEATURE_FOG

				if ( material.fogVolume ) {

					fogMaterial = material;
					fogMaterial.fogVolume = surfaceHit.side == 1.0;
					i -= sign( transmissiveTraversals );
					transmissiveTraversals --;
					continue;

				}

				#endif

				if ( ! material.castShadow && isShadowRay ) {

					continue;

				}

				mat4x2 uv = readHitUvs( attributesArray, surfaceHit.barycoord, surfaceHit.faceIndices.xyz );
				vec4 vertexColor = textureSampleBarycoord( attributesArray, ATTR_COLOR, surfaceHit.barycoord, surfaceHit.faceIndices.xyz );

				// albedo
				vec4 albedo = vec4( material.color, material.opacity );
				if ( material.map != - 1 ) {

					vec3 uvPrime = transformMapUv( material.mapTransform, uv );
					albedo *= texture2D( textures, vec3( uvPrime.xy, material.map ) );

				}

				if ( material.vertexColors ) {

					albedo *= vertexColor;

				}

				// alphaMap
				if ( material.alphaMap != - 1 ) {

					vec3 uvPrime = transformMapUv( material.alphaMapTransform, uv );
					albedo.a *= texture2D( textures, vec3( uvPrime.xy, material.alphaMap ) ).x;

				}

				// transmission
				float transmission = material.transmission;
				if ( material.transmissionMap != - 1 ) {

					vec3 uvPrime = transformMapUv( material.transmissionMapTransform, uv );
					transmission *= texture2D( textures, vec3( uvPrime.xy, material.transmissionMap ) ).r;

				}

				// metalness
				float metalness = material.metalness;
				if ( material.metalnessMap != - 1 ) {

					vec3 uvPrime = transformMapUv( material.metalnessMapTransform, uv );
					metalness *= texture2D( textures, vec3( uvPrime.xy, material.metalnessMap ) ).b;

				}

				float alphaTest = material.alphaTest;
				bool useAlphaTest = alphaTest != 0.0;
				float transmissionFactor = ( 1.0 - metalness ) * transmission;
				if (
					transmissionFactor < rand( 9 ) && ! (
						// material sidedness
						material.side != 0.0 && surfaceHit.side == material.side

						// alpha test
						|| useAlphaTest && albedo.a < alphaTest

						// opacity
						|| material.transparent && ! useAlphaTest && albedo.a < rand( 10 )
					)
				) {

					result = true;
					break;

				}

				if ( surfaceHit.side == 1.0 && isEntering ) {

					// only attenuate by surface color on the way in
					color *= mix( vec3( 1.0 ), albedo.rgb, transmissionFactor );

				} else if ( surfaceHit.side == - 1.0 ) {

					// attenuate by medium once we hit the opposite side of the model
					color *= transmissionAttenuation( surfaceHit.dist, material.attenuationColor, material.attenuationDistance );

				}

				bool isTransmissiveRay = dot( ray.direction, surfaceHit.faceNormal * surfaceHit.side ) < 0.0;
				if ( ( isTransmissiveRay || isEntering ) && transmissiveTraversals > 0 ) {

					i -= sign( transmissiveTraversals );
					transmissiveTraversals --;

				}

			} else {

				result = false;
				break;

			}

		}

		// reset the bounce index
		sobolBounceIndex = originalBounceIndex;
		return result;

	}

`
);

// npm/three-gpu-pathtracer/src/materials/pathtracing/glsl/camera_util_functions.glsl.js
var camera_util_functions = (
  /* glsl */
  `

	vec3 ndcToRayOrigin( vec2 coord ) {

		vec4 rayOrigin4 = cameraWorldMatrix * invProjectionMatrix * vec4( coord, - 1.0, 1.0 );
		return rayOrigin4.xyz / rayOrigin4.w;
	}

	Ray getCameraRay() {

		vec2 ssd = vec2( 1.0 ) / resolution;

		// Jitter the camera ray by finding a uv coordinate at a random sample
		// around this pixel's UV coordinate for AA
		vec2 ruv = rand2( 0 );
		vec2 jitteredUv = vUv + vec2( tentFilter( ruv.x ) * ssd.x, tentFilter( ruv.y ) * ssd.y );
		Ray ray;

		#if CAMERA_TYPE == 2

			// Equirectangular projection
			vec4 rayDirection4 = vec4( equirectUvToDirection( jitteredUv ), 0.0 );
			vec4 rayOrigin4 = vec4( 0.0, 0.0, 0.0, 1.0 );

			rayDirection4 = cameraWorldMatrix * rayDirection4;
			rayOrigin4 = cameraWorldMatrix * rayOrigin4;

			ray.direction = normalize( rayDirection4.xyz );
			ray.origin = rayOrigin4.xyz / rayOrigin4.w;

		#else

			// get [- 1, 1] normalized device coordinates
			vec2 ndc = 2.0 * jitteredUv - vec2( 1.0 );
			ray.origin = ndcToRayOrigin( ndc );

			#if CAMERA_TYPE == 1

				// Orthographic projection
				ray.direction = ( cameraWorldMatrix * vec4( 0.0, 0.0, - 1.0, 0.0 ) ).xyz;
				ray.direction = normalize( ray.direction );

			#else

				// Perspective projection
				ray.direction = normalize( mat3( cameraWorldMatrix ) * ( invProjectionMatrix * vec4( ndc, 0.0, 1.0 ) ).xyz );

			#endif

		#endif

		#if FEATURE_DOF
		{

			// depth of field
			vec3 focalPoint = ray.origin + normalize( ray.direction ) * physicalCamera.focusDistance;

			// get the aperture sample
			// if blades === 0 then we assume a circle
			vec3 shapeUVW= rand3( 1 );
			int blades = physicalCamera.apertureBlades;
			float anamorphicRatio = physicalCamera.anamorphicRatio;
			vec2 apertureSample = sampleAperture( blades, shapeUVW );
			apertureSample *= physicalCamera.bokehSize * 0.5 * 1e-3;

			// rotate the aperture shape
			apertureSample =
				rotateVector( apertureSample, physicalCamera.apertureRotation ) *
				saturate( vec2( anamorphicRatio, 1.0 / anamorphicRatio ) );

			// create the new ray
			ray.origin += ( cameraWorldMatrix * vec4( apertureSample, 0.0, 0.0 ) ).xyz;
			ray.direction = focalPoint - ray.origin;

		}
		#endif

		ray.direction = normalize( ray.direction );

		return ray;

	}

`
);

// npm/three-gpu-pathtracer/src/materials/pathtracing/glsl/direct_light_contribution_function.glsl.js
var direct_light_contribution_function = (
  /*glsl*/
  `

	vec3 directLightContribution( vec3 worldWo, SurfaceRecord surf, RenderState state, vec3 rayOrigin ) {

		vec3 result = vec3( 0.0 );

		// uniformly pick a light or environment map
		if( lightsDenom != 0.0 && rand( 5 ) < float( lights.count ) / lightsDenom ) {

			// sample a light or environment
			LightRecord lightRec = randomLightSample( lights.tex, iesProfiles, lights.count, rayOrigin, rand3( 6 ) );

			bool isSampleBelowSurface = ! surf.volumeParticle && dot( surf.faceNormal, lightRec.direction ) < 0.0;
			if ( isSampleBelowSurface ) {

				lightRec.pdf = 0.0;

			}

			// check if a ray could even reach the light area
			Ray lightRay;
			lightRay.origin = rayOrigin;
			lightRay.direction = lightRec.direction;
			vec3 attenuatedColor;
			if (
				lightRec.pdf > 0.0 &&
				isDirectionValid( lightRec.direction, surf.normal, surf.faceNormal ) &&
				! attenuateHit( state, lightRay, lightRec.dist, attenuatedColor )
			) {

				// get the material pdf
				vec3 sampleColor;
				float lightMaterialPdf = bsdfResult( worldWo, lightRec.direction, surf, sampleColor );
				bool isValidSampleColor = all( greaterThanEqual( sampleColor, vec3( 0.0 ) ) );
				if ( lightMaterialPdf > 0.0 && isValidSampleColor ) {

					// weight the direct light contribution
					float lightPdf = lightRec.pdf / lightsDenom;
					float misWeight = lightRec.type == SPOT_LIGHT_TYPE || lightRec.type == DIR_LIGHT_TYPE || lightRec.type == POINT_LIGHT_TYPE ? 1.0 : misHeuristic( lightPdf, lightMaterialPdf );
					result = attenuatedColor * lightRec.emission * state.throughputColor * sampleColor * misWeight / lightPdf;

				}

			}

		} else if ( envMapInfo.totalSum != 0.0 && environmentIntensity != 0.0 ) {

			// find a sample in the environment map to include in the contribution
			vec3 envColor, envDirection;
			float envPdf = sampleEquirectProbability( rand2( 7 ), envColor, envDirection );
			envDirection = invEnvRotation3x3 * envDirection;

			// this env sampling is not set up for transmissive sampling and yields overly bright
			// results so we ignore the sample in this case.
			// TODO: this should be improved but how? The env samples could traverse a few layers?
			bool isSampleBelowSurface = ! surf.volumeParticle && dot( surf.faceNormal, envDirection ) < 0.0;
			if ( isSampleBelowSurface ) {

				envPdf = 0.0;

			}

			// check if a ray could even reach the surface
			Ray envRay;
			envRay.origin = rayOrigin;
			envRay.direction = envDirection;
			vec3 attenuatedColor;
			if (
				envPdf > 0.0 &&
				isDirectionValid( envDirection, surf.normal, surf.faceNormal ) &&
				! attenuateHit( state, envRay, INFINITY, attenuatedColor )
			) {

				// get the material pdf
				vec3 sampleColor;
				float envMaterialPdf = bsdfResult( worldWo, envDirection, surf, sampleColor );
				bool isValidSampleColor = all( greaterThanEqual( sampleColor, vec3( 0.0 ) ) );
				if ( envMaterialPdf > 0.0 && isValidSampleColor ) {

					// weight the direct light contribution
					envPdf /= lightsDenom;
					float misWeight = misHeuristic( envPdf, envMaterialPdf );
					result = attenuatedColor * environmentIntensity * envColor * state.throughputColor * sampleColor * misWeight / envPdf;

				}

			}

		}

		// Function changed to have a single return statement to potentially help with crashes on Mac OS.
		// See issue #470
		return result;

	}

`
);

// npm/three-gpu-pathtracer/src/materials/pathtracing/glsl/get_surface_record_function.glsl.js
var get_surface_record_function = (
  /* glsl */
  `

	#define SKIP_SURFACE 0
	#define HIT_SURFACE 1
	int getSurfaceRecord(
		Material material, SurfaceHit surfaceHit, sampler2DArray attributesArray,
		float accumulatedRoughness, vec3 hitPosition,
		inout SurfaceRecord surf
	) {

		if ( material.fogVolume ) {

			vec3 normal = vec3( 0, 0, 1 );

			SurfaceRecord fogSurface;
			fogSurface.volumeParticle = true;
			fogSurface.color = material.color;
			fogSurface.emission = material.emissiveIntensity * material.emissive;
			fogSurface.normal = normal;
			fogSurface.faceNormal = normal;
			fogSurface.clearcoatNormal = normal;

			surf = fogSurface;
			return HIT_SURFACE;

		}

		// uv coord for textures
		mat4x2 uv = readHitUvs( attributesArray, surfaceHit.barycoord, surfaceHit.faceIndices.xyz );
		vec4 vertexColor = textureSampleBarycoord( attributesArray, ATTR_COLOR, surfaceHit.barycoord, surfaceHit.faceIndices.xyz );

		// albedo
		vec4 albedo = vec4( material.color, material.opacity );
		if ( material.map != - 1 ) {

			vec3 uvPrime = transformMapUv( material.mapTransform, uv );
			albedo *= texture2D( textures, vec3( uvPrime.xy, material.map ) );

		}

		if ( material.vertexColors ) {

			albedo *= vertexColor;

		}

		// alphaMap
		if ( material.alphaMap != - 1 ) {

			vec3 uvPrime = transformMapUv( material.alphaMapTransform, uv );
			albedo.a *= texture2D( textures, vec3( uvPrime.xy, material.alphaMap ) ).x;

		}

		// possibly skip this sample if it's transparent, alpha test is enabled, or we hit the wrong material side
		// and it's single sided.
		// - alpha test is disabled when it === 0
		// - the material sidedness test is complicated because we want light to pass through the back side but still
		// be able to see the front side. This boolean checks if the side we hit is the front side on the first ray
		// and we're rendering the other then we skip it. Do the opposite on subsequent bounces to get incoming light.
		float alphaTest = material.alphaTest;
		bool useAlphaTest = alphaTest != 0.0;
		if (
			// material sidedness
			material.side != 0.0 && surfaceHit.side != material.side

			// alpha test
			|| useAlphaTest && albedo.a < alphaTest

			// opacity
			|| material.transparent && ! useAlphaTest && albedo.a < rand( 3 )
		) {

			return SKIP_SURFACE;

		}

		// fetch the interpolated smooth normal
		vec3 normal = normalize( textureSampleBarycoord(
			attributesArray,
			ATTR_NORMAL,
			surfaceHit.barycoord,
			surfaceHit.faceIndices.xyz
		).xyz );

		// roughness
		float roughness = material.roughness;
		if ( material.roughnessMap != - 1 ) {

			vec3 uvPrime = transformMapUv( material.roughnessMapTransform, uv );
			roughness *= texture2D( textures, vec3( uvPrime.xy, material.roughnessMap ) ).g;

		}

		// metalness
		float metalness = material.metalness;
		if ( material.metalnessMap != - 1 ) {

			vec3 uvPrime = transformMapUv( material.metalnessMapTransform, uv );
			metalness *= texture2D( textures, vec3( uvPrime.xy, material.metalnessMap ) ).b;

		}

		// emission
		vec3 emission = material.emissiveIntensity * material.emissive;
		if ( material.emissiveMap != - 1 ) {

			vec3 uvPrime = transformMapUv( material.emissiveMapTransform, uv );
			emission *= texture2D( textures, vec3( uvPrime.xy, material.emissiveMap ) ).xyz;

		}

		// transmission
		float transmission = material.transmission;
		if ( material.transmissionMap != - 1 ) {

			vec3 uvPrime = transformMapUv( material.transmissionMapTransform, uv );
			transmission *= texture2D( textures, vec3( uvPrime.xy, material.transmissionMap ) ).r;

		}

		// normal
		if ( material.flatShading ) {

			// if we're rendering a flat shaded object then use the face normals - the face normal
			// is provided based on the side the ray hits the mesh so flip it to align with the
			// interpolated vertex normals.
			normal = surfaceHit.faceNormal * surfaceHit.side;

		}

		vec3 baseNormal = normal;
		if ( material.normalMap != - 1 ) {

			vec4 tangentSample = textureSampleBarycoord(
				attributesArray,
				ATTR_TANGENT,
				surfaceHit.barycoord,
				surfaceHit.faceIndices.xyz
			);

			// some provided tangents can be malformed (0, 0, 0) causing the normal to be degenerate
			// resulting in NaNs and slow path tracing.
			if ( length( tangentSample.xyz ) > 0.0 ) {

				vec3 tangent = normalize( tangentSample.xyz );
				vec3 bitangent = normalize( cross( normal, tangent ) * tangentSample.w );
				mat3 vTBN = mat3( tangent, bitangent, normal );

				vec3 uvPrime = transformMapUv( material.normalMapTransform, uv );
				vec3 texNormal = texture2D( textures, vec3( uvPrime.xy, material.normalMap ) ).xyz * 2.0 - 1.0;
				texNormal.xy *= material.normalScale;
				normal = vTBN * texNormal;

			}

		}

		#if FEATURE_WUYING_WOOD
		if (material.woodSettings.x > 0.) {
			uint first=material.woodOffset;
			vec4 c0=texelFetch1D(materials,first),c1=texelFetch1D(materials,first+1u);
			vec4 rx=texelFetch1D(materials,first+2u),ry=texelFetch1D(materials,first+3u),rz=texelFetch1D(materials,first+4u);
			vec4 p=vec4(hitPosition,1.);
			vec3 generated=vec3(dot(rx,p),dot(ry,p),dot(rz,p));
			vec4 field=wyWoodWaveGradient(generated,material.woodSettings.x,material.woodSettings.y);
			if(c0.w>.5)albedo.rgb=mix(c0.rgb,c1.rgb,field.w);
			if(material.woodSettings.z>=0.)roughness=mix(material.woodSettings.z,material.woodSettings.w,field.w);
			if(c1.w>0.){
				vec3 gradient=rx.xyz*field.x+ry.xyz*field.y+rz.xyz*field.z;
				vec3 N=normalize(normal);
				vec3 face=surfaceHit.faceNormal*surfaceHit.side;
				float alignment=dot(N,face);
				if(abs(alignment)>1e-7){
					vec3 tangentGradient=gradient-face*(dot(N,gradient)/alignment);
					normal=normalize(mix(N,normalize(N-c1.w*tangentGradient),.2));
				}
			}
		}
		#endif
		normal *= surfaceHit.side;

		// clearcoat
		float clearcoat = material.clearcoat;
		if ( material.clearcoatMap != - 1 ) {

			vec3 uvPrime = transformMapUv( material.clearcoatMapTransform, uv );
			clearcoat *= texture2D( textures, vec3( uvPrime.xy, material.clearcoatMap ) ).r;

		}

		// clearcoatRoughness
		float clearcoatRoughness = material.clearcoatRoughness;
		if ( material.clearcoatRoughnessMap != - 1 ) {

			vec3 uvPrime = transformMapUv( material.clearcoatRoughnessMapTransform, uv );
			clearcoatRoughness *= texture2D( textures, vec3( uvPrime.xy, material.clearcoatRoughnessMap ) ).g;

		}

		// clearcoatNormal
		vec3 clearcoatNormal = baseNormal;
		if ( material.clearcoatNormalMap != - 1 ) {

			vec4 tangentSample = textureSampleBarycoord(
				attributesArray,
				ATTR_TANGENT,
				surfaceHit.barycoord,
				surfaceHit.faceIndices.xyz
			);

			// some provided tangents can be malformed (0, 0, 0) causing the normal to be degenerate
			// resulting in NaNs and slow path tracing.
			if ( length( tangentSample.xyz ) > 0.0 ) {

				vec3 tangent = normalize( tangentSample.xyz );
				vec3 bitangent = normalize( cross( clearcoatNormal, tangent ) * tangentSample.w );
				mat3 vTBN = mat3( tangent, bitangent, clearcoatNormal );

				vec3 uvPrime = transformMapUv( material.clearcoatNormalMapTransform, uv );
				vec3 texNormal = texture2D( textures, vec3( uvPrime.xy, material.clearcoatNormalMap ) ).xyz * 2.0 - 1.0;
				texNormal.xy *= material.clearcoatNormalScale;
				clearcoatNormal = vTBN * texNormal;

			}

		}

		clearcoatNormal *= surfaceHit.side;

		// sheenColor
		vec3 sheenColor = material.sheenColor;
		if ( material.sheenColorMap != - 1 ) {

			vec3 uvPrime = transformMapUv( material.sheenColorMapTransform, uv );
			sheenColor *= texture2D( textures, vec3( uvPrime.xy, material.sheenColorMap ) ).rgb;

		}

		// sheenRoughness
		float sheenRoughness = material.sheenRoughness;
		if ( material.sheenRoughnessMap != - 1 ) {

			vec3 uvPrime = transformMapUv( material.sheenRoughnessMapTransform, uv );
			sheenRoughness *= texture2D( textures, vec3( uvPrime.xy, material.sheenRoughnessMap ) ).a;

		}

		// iridescence
		float iridescence = material.iridescence;
		if ( material.iridescenceMap != - 1 ) {

			vec3 uvPrime = transformMapUv( material.iridescenceMapTransform, uv );
			iridescence *= texture2D( textures, vec3( uvPrime.xy, material.iridescenceMap ) ).r;

		}

		// iridescence thickness
		float iridescenceThickness = material.iridescenceThicknessMaximum;
		if ( material.iridescenceThicknessMap != - 1 ) {

			vec3 uvPrime = transformMapUv( material.iridescenceThicknessMapTransform, uv );
			float iridescenceThicknessSampled = texture2D( textures, vec3( uvPrime.xy, material.iridescenceThicknessMap ) ).g;
			iridescenceThickness = mix( material.iridescenceThicknessMinimum, material.iridescenceThicknessMaximum, iridescenceThicknessSampled );

		}

		iridescence = iridescenceThickness == 0.0 ? 0.0 : iridescence;

		// specular color
		vec3 specularColor = material.specularColor;
		if ( material.specularColorMap != - 1 ) {

			vec3 uvPrime = transformMapUv( material.specularColorMapTransform, uv );
			specularColor *= texture2D( textures, vec3( uvPrime.xy, material.specularColorMap ) ).rgb;

		}

		// specular intensity
		float specularIntensity = material.specularIntensity;
		if ( material.specularIntensityMap != - 1 ) {

			vec3 uvPrime = transformMapUv( material.specularIntensityMapTransform, uv );
			specularIntensity *= texture2D( textures, vec3( uvPrime.xy, material.specularIntensityMap ) ).a;

		}

		surf.volumeParticle = false;

		surf.faceNormal = surfaceHit.faceNormal;
		surf.normal = normal;

		surf.metalness = metalness;
		surf.color = albedo.rgb;
		surf.emission = emission;

		surf.ior = material.ior;
		surf.transmission = transmission;
		surf.thinFilm = material.thinFilm;
		surf.attenuationColor = material.attenuationColor;
		surf.attenuationDistance = material.attenuationDistance;

		surf.clearcoatNormal = clearcoatNormal;
		surf.clearcoat = clearcoat;

		surf.sheen = material.sheen;
		surf.sheenColor = sheenColor;

		surf.iridescence = iridescence;
		surf.iridescenceIor = material.iridescenceIor;
		surf.iridescenceThickness = iridescenceThickness;

		surf.specularColor = specularColor;
		surf.specularIntensity = specularIntensity;

		// apply perceptual roughness factor from gltf. sheen perceptual roughness is
		// applied by its brdf function
		// https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html#microfacet-surfaces
		surf.roughness = roughness * roughness;
		surf.clearcoatRoughness = clearcoatRoughness * clearcoatRoughness;
		surf.sheenRoughness = sheenRoughness;

		// frontFace is used to determine transmissive properties and PDF. If no transmission is used
		// then we can just always assume this is a front face.
		surf.frontFace = surfaceHit.side == 1.0 || transmission == 0.0;
		surf.eta = material.thinFilm || surf.frontFace ? 1.0 / material.ior : material.ior;
		surf.f0 = iorRatioToF0( surf.eta );

		// Compute the filtered roughness value to use during specular reflection computations.
		// The accumulated roughness value is scaled by a user setting and a "magic value" of 5.0.
		// If we're exiting something transmissive then scale the factor down significantly so we can retain
		// sharp internal reflections
		surf.filteredRoughness = applyFilteredGlossy( surf.roughness, accumulatedRoughness );
		surf.filteredClearcoatRoughness = applyFilteredGlossy( surf.clearcoatRoughness, accumulatedRoughness );

		// get the normal frames
		surf.normalBasis = getBasisFromNormal( surf.normal );
		surf.normalInvBasis = inverse( surf.normalBasis );

		surf.clearcoatBasis = getBasisFromNormal( surf.clearcoatNormal );
		surf.clearcoatInvBasis = inverse( surf.clearcoatBasis );

		return HIT_SURFACE;

	}
`
);

// npm/three-gpu-pathtracer/src/materials/pathtracing/glsl/render_structs.glsl.js
var render_structs = (
  /* glsl */
  `

	struct Ray {

		vec3 origin;
		vec3 direction;

	};

	struct SurfaceHit {

		uvec4 faceIndices;
		vec3 barycoord;
		vec3 faceNormal;
		float side;
		float dist;

	};

	struct RenderState {

		bool firstRay;
		bool transmissiveRay;
		bool isShadowRay;
		float accumulatedRoughness;
		int transmissiveTraversals;
		int traversals;
		uint depth;
		vec3 throughputColor;
		Material fogMaterial;

	};

	RenderState initRenderState() {

		RenderState result;
		result.firstRay = true;
		result.transmissiveRay = true;
		result.isShadowRay = false;
		result.accumulatedRoughness = 0.0;
		result.transmissiveTraversals = 0;
		result.traversals = 0;
		result.throughputColor = vec3( 1.0 );
		result.depth = 0u;
		result.fogMaterial.fogVolume = false;
		return result;

	}

`
);

// npm/three-gpu-pathtracer/src/materials/pathtracing/glsl/trace_scene_function.glsl.js
var trace_scene_function = (
  /* glsl */
  `

	#define NO_HIT 0
	#define SURFACE_HIT 1
	#define LIGHT_HIT 2
	#define FOG_HIT 3

	// Passing the global variable 'lights' into this function caused shader program errors.
	// So global variables like 'lights' and 'bvh' were moved out of the function parameters.
	// For more information, refer to: https://github.com/gkjohnson/three-gpu-pathtracer/pull/457
	int traceScene(
		Ray ray, Material fogMaterial, inout SurfaceHit surfaceHit
	) {

		int result = NO_HIT;
		bool hit = bvhIntersectFirstHit( bvh, ray.origin, ray.direction, surfaceHit.faceIndices, surfaceHit.faceNormal, surfaceHit.barycoord, surfaceHit.side, surfaceHit.dist );

		#if FEATURE_FOG

		if ( fogMaterial.fogVolume ) {

			// offset the distance so we don't run into issues with particles on the same surface
			// as other objects
			float particleDist = intersectFogVolume( fogMaterial, rand( 1 ) );
			if ( particleDist + RAY_OFFSET < surfaceHit.dist ) {

				surfaceHit.side = 1.0;
				surfaceHit.faceNormal = normalize( - ray.direction );
				surfaceHit.dist = particleDist;
				return FOG_HIT;

			}

		}

		#endif

		if ( hit ) {

			result = SURFACE_HIT;

		}

		return result;

	}

`
);

// npm/three-gpu-pathtracer/src/materials/pathtracing/PhysicalPathTracingMaterial.js
var PhysicalPathTracingMaterial = class extends MaterialBase {
  onBeforeRender() {
    this.setDefine("FEATURE_DOF", this.physicalCamera.bokehSize === 0 ? 0 : 1), this.setDefine("FEATURE_BACKGROUND_MAP", this.backgroundMap ? 1 : 0), this.setDefine("FEATURE_FOG", this.materials.features.isUsed("FOG") ? 1 : 0);
  }
  constructor(parameters) {
    super({
      transparent: !0,
      depthWrite: !1,
      defines: {
        FEATURE_MIS: 1,
        FEATURE_RUSSIAN_ROULETTE: 1,
        FEATURE_DOF: 1,
        FEATURE_BACKGROUND_MAP: 0,
        FEATURE_FOG: 1,
        // 0 = PCG
        // 1 = Sobol
        // 2 = Stratified List
        RANDOM_TYPE: 2,
        // 0 = Perspective
        // 1 = Orthographic
        // 2 = Equirectangular
        CAMERA_TYPE: 0,
        DEBUG_MODE: 0,
        ATTR_NORMAL: 0,
        ATTR_TANGENT: 1,
        ATTR_UV: 2,
        ATTR_COLOR: 3,
        TRACE_UV_COUNT: 1, FEATURE_WUYING_WOOD: 0,
        MATERIAL_PIXELS
      },
      uniforms: {
        // path trace uniforms
        resolution: { value: new Vector26() },
        opacity: { value: 1 },
        bounces: { value: 10 },
        transmissiveBounces: { value: 10 },
        filterGlossyFactor: { value: 0 },
        // camera uniforms
        physicalCamera: { value: new PhysicalCameraUniform() },
        cameraWorldMatrix: { value: new Matrix411() },
        invProjectionMatrix: { value: new Matrix411() },
        // scene uniforms
        bvh: { value: new MeshBVHUniformStruct() },
        attributesArray: { value: new AttributesTextureArray() },
        materialIndexAttribute: { value: new UIntVertexAttributeTexture() },
        materials: { value: new MaterialsTexture() },
        textures: { value: new RenderTarget2DArray().texture },
        // light uniforms
        lights: { value: new LightsInfoUniformStruct() },
        iesProfiles: { value: new RenderTarget2DArray(360, 180, {
          type: HalfFloatType2,
          wrapS: ClampToEdgeWrapping4,
          wrapT: ClampToEdgeWrapping4
        }).texture },
        environmentIntensity: { value: 1 },
        environmentRotation: { value: new Matrix411() },
        envMapInfo: { value: new EquirectHdrInfoUniform() },
        // background uniforms
        backgroundBlur: { value: 0 },
        backgroundMap: { value: null },
        backgroundAlpha: { value: 1 },
        backgroundIntensity: { value: 1 },
        backgroundRotation: { value: new Matrix411() },
        // randomness uniforms
        seed: { value: 0 },
        sobolTexture: { value: null },
        stratifiedTexture: { value: new StratifiedSamplesTexture() },
        stratifiedOffsetTexture: { value: new BlueNoiseTexture(64, 1) }
      },
      vertexShader: (
        /* glsl */
        `

				varying vec2 vUv;
				void main() {

					vec4 mvPosition = vec4( position, 1.0 );
					mvPosition = modelViewMatrix * mvPosition;
					gl_Position = projectionMatrix * mvPosition;

					vUv = uv;

				}

			`
      ),
      fragmentShader: (
        /* glsl */
        `
				#define RAY_OFFSET 1e-4
				#define INFINITY 1e20

				precision highp isampler2D;
				precision highp usampler2D;
				precision highp sampler2DArray;
				vec4 envMapTexelToLinear( vec4 a ) { return a; }
				#include <common>

				// bvh intersection
				${BVHShaderGLSL_exports.common_functions}
				${BVHShaderGLSL_exports.bvh_struct_definitions}
				${BVHShaderGLSL_exports.bvh_ray_functions}

				// uniform structs
				${camera_struct}
				${lights_struct}
				${equirect_struct}
				${material_struct}
				${surface_record_struct}

				// random
				#if RANDOM_TYPE == 2 	// Stratified List

					${stratified_functions}

				#elif RANDOM_TYPE == 1 	// Sobol

					${pcg_functions}
					${sobol_common}
					${sobol_functions}

					#define rand(v) sobol(v)
					#define rand2(v) sobol2(v)
					#define rand3(v) sobol3(v)
					#define rand4(v) sobol4(v)

				#else 					// PCG

				${pcg_functions}

					// Using the sobol functions seems to break the the compiler on MacOS
					// - specifically the "sobolReverseBits" function.
					uint sobolPixelIndex = 0u;
					uint sobolPathIndex = 0u;
					uint sobolBounceIndex = 0u;

					#define rand(v) pcgRand()
					#define rand2(v) pcgRand2()
					#define rand3(v) pcgRand3()
					#define rand4(v) pcgRand4()

				#endif

				// common
				${texture_sample_functions}
				${fresnel_functions}
				${util_functions}
				${math_functions}
				${shape_intersection_functions}

				// environment
				uniform EquirectHdrInfo envMapInfo;
				uniform mat4 environmentRotation;
				uniform float environmentIntensity;

				// lighting
				uniform sampler2DArray iesProfiles;
				uniform LightsInfo lights;

				// background
				uniform float backgroundBlur;
				uniform float backgroundAlpha;
				#if FEATURE_BACKGROUND_MAP

				uniform sampler2D backgroundMap;
				uniform mat4 backgroundRotation;
				uniform float backgroundIntensity;

				#endif

				// camera
				uniform mat4 cameraWorldMatrix;
				uniform mat4 invProjectionMatrix;
				#if FEATURE_DOF

				uniform PhysicalCamera physicalCamera;

				#endif

				// geometry
				uniform sampler2DArray attributesArray;
				uniform usampler2D materialIndexAttribute;
				uniform sampler2D materials;
				uniform sampler2DArray textures;
				uniform BVH bvh;

				// path tracer
				uniform int bounces;
				uniform int transmissiveBounces;
				uniform float filterGlossyFactor;
				uniform int seed;

				// image
				uniform vec2 resolution;
				uniform float opacity;

				varying vec2 vUv;

				// globals
				mat3 envRotation3x3;
				mat3 invEnvRotation3x3;
				float lightsDenom;

				// sampling
				${shape_sampling_functions}
				${equirect_functions}
				${light_sampling_functions}

				${inside_fog_volume_function}
				${ggx_functions}
				${sheen_functions}
				${iridescence_functions}
				${fog_functions}
				${bsdf_functions}

				float applyFilteredGlossy( float roughness, float accumulatedRoughness ) {

					return clamp(
						max(
							roughness,
							accumulatedRoughness * filterGlossyFactor * 5.0 ),
						0.0,
						1.0
					);

				}

				vec3 sampleBackground( vec3 direction, vec2 uv ) {

					vec3 sampleDir = sampleHemisphere( direction, uv ) * 0.5 * backgroundBlur;

					#if FEATURE_BACKGROUND_MAP

					sampleDir = normalize( mat3( backgroundRotation ) * direction + sampleDir );
					return backgroundIntensity * sampleEquirectColor( backgroundMap, sampleDir );

					#else

					sampleDir = normalize( envRotation3x3 * direction + sampleDir );
					return environmentIntensity * sampleEquirectColor( envMapInfo.map, sampleDir );

					#endif

				}

				${render_structs}
				${camera_util_functions}
				${trace_scene_function}
				${attenuate_hit_function}
				${direct_light_contribution_function}
				#if FEATURE_WUYING_WOOD
				${woodWaveGLSL}
				#endif
				${get_surface_record_function}

				void main() {

					// init
					rng_initialize( gl_FragCoord.xy, seed );
					sobolPixelIndex = ( uint( gl_FragCoord.x ) << 16 ) | uint( gl_FragCoord.y );
					sobolPathIndex = uint( seed );

					// get camera ray
					Ray ray = getCameraRay();

					// inverse environment rotation
					envRotation3x3 = mat3( environmentRotation );
					invEnvRotation3x3 = inverse( envRotation3x3 );
					lightsDenom =
						( environmentIntensity == 0.0 || envMapInfo.totalSum == 0.0 ) && lights.count != 0u ?
							float( lights.count ) :
							float( lights.count + 1u );

					// final color
					gl_FragColor = vec4( 0, 0, 0, 1 );

					// surface results
					SurfaceHit surfaceHit;
					ScatterRecord scatterRec;

					// path tracing state
					RenderState state = initRenderState();
					state.transmissiveTraversals = transmissiveBounces;
					#if FEATURE_FOG

					state.fogMaterial.fogVolume = bvhIntersectFogVolumeHit(
						ray.origin, - ray.direction,
						materialIndexAttribute, materials,
						state.fogMaterial
					);

					#endif

					for ( int i = 0; i < bounces; i ++ ) {

						sobolBounceIndex ++;

						state.depth ++;
						state.traversals = bounces - i;
						state.firstRay = i == 0 && state.transmissiveTraversals == transmissiveBounces;

						int hitType = traceScene( ray, state.fogMaterial, surfaceHit );

						// check if we intersect any lights and accumulate the light contribution
						// TODO: we can add support for light surface rendering in the else condition if we
						// add the ability to toggle visibility of the the light
						if ( ! state.firstRay && ! state.transmissiveRay ) {

							LightRecord lightRec;
							float lightDist = hitType == NO_HIT ? INFINITY : surfaceHit.dist;
							for ( uint i = 0u; i < lights.count; i ++ ) {

								if (
									intersectLightAtIndex( lights.tex, ray.origin, ray.direction, i, lightRec ) &&
									lightRec.dist < lightDist
								) {

									#if FEATURE_MIS

									// weight the contribution
									// NOTE: Only area lights are supported for forward sampling and can be hit
									float misWeight = misHeuristic( scatterRec.pdf, lightRec.pdf / lightsDenom );
									gl_FragColor.rgb += lightRec.emission * state.throughputColor * misWeight;

									#else

									gl_FragColor.rgb += lightRec.emission * state.throughputColor;

									#endif

								}

							}

						}

						if ( hitType == NO_HIT ) {

							if ( state.firstRay || state.transmissiveRay ) {

								gl_FragColor.rgb += sampleBackground( ray.direction, rand2( 2 ) ) * state.throughputColor;
								gl_FragColor.a = backgroundAlpha;

							} else {

								#if FEATURE_MIS

								// get the PDF of the hit envmap point
								vec3 envColor;
								float envPdf = sampleEquirect( envRotation3x3 * ray.direction, envColor );
								envPdf /= lightsDenom;

								// and weight the contribution
								float misWeight = misHeuristic( scatterRec.pdf, envPdf );
								gl_FragColor.rgb += environmentIntensity * envColor * state.throughputColor * misWeight;

								#else

								gl_FragColor.rgb +=
									environmentIntensity *
									sampleEquirectColor( envMapInfo.map, envRotation3x3 * ray.direction ) *
									state.throughputColor;

								#endif

							}
							break;

						}

						uint materialIndex = uTexelFetch1D( materialIndexAttribute, surfaceHit.faceIndices.x ).r;
						Material material = readMaterialInfo( materials, materialIndex );

						#if FEATURE_FOG

						if ( hitType == FOG_HIT ) {

							material = state.fogMaterial;
							state.accumulatedRoughness += 0.2;

						} else if ( material.fogVolume ) {

							state.fogMaterial = material;
							state.fogMaterial.fogVolume = surfaceHit.side == 1.0;

							ray.origin = stepRayOrigin( ray.origin, ray.direction, - surfaceHit.faceNormal, surfaceHit.dist );

							i -= sign( state.transmissiveTraversals );
							state.transmissiveTraversals -= sign( state.transmissiveTraversals );
							continue;

						}

						#endif

						// early out if this is a matte material
						if ( material.matte && state.firstRay ) {

							gl_FragColor = vec4( 0.0 );
							break;

						}

						// if we've determined that this is a shadow ray and we've hit an item with no shadow casting
						// then skip it
						if ( ! material.castShadow && state.isShadowRay ) {

							ray.origin = stepRayOrigin( ray.origin, ray.direction, - surfaceHit.faceNormal, surfaceHit.dist );
							continue;

						}

						SurfaceRecord surf;
						if (
							getSurfaceRecord(
								material, surfaceHit, attributesArray, state.accumulatedRoughness, ray.origin+ray.direction*surfaceHit.dist,
								surf
							) == SKIP_SURFACE
						) {

							// only allow a limited number of transparency discards otherwise we could
							// crash the context with too long a loop.
							i -= sign( state.transmissiveTraversals );
							state.transmissiveTraversals -= sign( state.transmissiveTraversals );

							ray.origin = stepRayOrigin( ray.origin, ray.direction, - surfaceHit.faceNormal, surfaceHit.dist );
							continue;

						}

						scatterRec = bsdfSample( - ray.direction, surf );
						state.isShadowRay = scatterRec.specularPdf < rand( 4 );

						bool isBelowSurface = ! surf.volumeParticle && dot( scatterRec.direction, surf.faceNormal ) < 0.0;
						vec3 hitPoint = stepRayOrigin( ray.origin, ray.direction, isBelowSurface ? - surf.faceNormal : surf.faceNormal, surfaceHit.dist );

						// next event estimation
						#if FEATURE_MIS

						gl_FragColor.rgb += directLightContribution( - ray.direction, surf, state, hitPoint );

						#endif

						// accumulate a roughness value to offset diffuse, specular, diffuse rays that have high contribution
						// to a single pixel resulting in fireflies
						// TODO: handle transmissive surfaces
						if ( ! surf.volumeParticle && ! isBelowSurface ) {

							// determine if this is a rough normal or not by checking how far off straight up it is
							vec3 halfVector = normalize( - ray.direction + scatterRec.direction );
							state.accumulatedRoughness += max(
								sin( acosApprox( dot( halfVector, surf.normal ) ) ),
								sin( acosApprox( dot( halfVector, surf.clearcoatNormal ) ) )
							);

							state.transmissiveRay = false;

						}

						// accumulate emissive color
						gl_FragColor.rgb += ( surf.emission * state.throughputColor );

						// skip the sample if our PDF or ray is impossible
						if ( scatterRec.pdf <= 0.0 || ! isDirectionValid( scatterRec.direction, surf.normal, surf.faceNormal ) ) {

							break;

						}

						// if we're bouncing around the inside a transmissive material then decrement
						// perform this separate from a bounce
						bool isTransmissiveRay = ! surf.volumeParticle && dot( scatterRec.direction, surf.faceNormal * surfaceHit.side ) < 0.0;
						if ( ( isTransmissiveRay || isBelowSurface ) && state.transmissiveTraversals > 0 ) {

							state.transmissiveTraversals --;
							i --;

						}

						//

						// handle throughput color transformation
						// attenuate the throughput color by the medium color
						if ( ! surf.frontFace ) {

							state.throughputColor *= transmissionAttenuation( surfaceHit.dist, surf.attenuationColor, surf.attenuationDistance );

						}

						#if FEATURE_RUSSIAN_ROULETTE

						// russian roulette path termination
						// https://www.arnoldrenderer.com/research/physically_based_shader_design_in_arnold.pdf
						uint minBounces = 3u;
						float depthProb = float( state.depth < minBounces );

						float rrProb = luminance( state.throughputColor * scatterRec.color / scatterRec.pdf );
						rrProb /= luminance( state.throughputColor );
						rrProb = sqrt( rrProb );
						rrProb = max( rrProb, depthProb );
						rrProb = min( rrProb, 1.0 );
						if ( rand( 8 ) > rrProb ) {

							break;

						}

						// perform sample clamping here to avoid bright pixels
						state.throughputColor *= min( 1.0 / rrProb, 20.0 );

						#endif

						// adjust the throughput and discard and exit if we find discard the sample if there are any NaNs
						state.throughputColor *= scatterRec.color / scatterRec.pdf;
						if ( any( isnan( state.throughputColor ) ) || any( isinf( state.throughputColor ) ) ) {

							break;

						}

						//

						// prepare for next ray
						ray.direction = scatterRec.direction;
						ray.origin = hitPoint;

					}

					gl_FragColor.a *= opacity;

					#if DEBUG_MODE == 1

					// output the number of rays checked in the path and number of
					// transmissive rays encountered.
					gl_FragColor.rgb = vec3(
						float( state.depth ),
						transmissiveBounces - state.transmissiveTraversals,
						0.0
					);
					gl_FragColor.a = 1.0;

					#endif

				}

			`
      )
    }), this.setValues(parameters);
  }
};

// npm/three-gpu-pathtracer/src/core/PathTracingRenderer.js
function* renderTask() {
  let {
    _renderer,
    _fsQuad,
    _blendQuad,
    _primaryTarget,
    _blendTargets,
    _sobolTarget,
    _subframe,
    alpha,
    material
  } = this, _ogScissor = new Vector42(), _ogViewport = new Vector42(), blendMaterial = _blendQuad.material, [blendTarget1, blendTarget2] = _blendTargets;
  for (; ; ) {
    alpha ? (blendMaterial.opacity = this._opacityFactor / (this.samples + 1), material.blending = NoBlending3, material.opacity = 1) : (material.opacity = this._opacityFactor / (this.samples + 1), material.blending = NormalBlending);
    let [subX, subY, subW, subH] = _subframe, w = _primaryTarget.width, h = _primaryTarget.height;
    material.resolution.set(w * subW, h * subH), material.sobolTexture = _sobolTarget.texture, material.stratifiedTexture.init(20, material.bounces + material.transmissiveBounces + 5), material.stratifiedTexture.next(), material.seed++;
    let tilesX = this.tiles.x || 1, tilesY = this.tiles.y || 1, totalTiles = tilesX * tilesY, pxSubW = Math.ceil(w * subW), pxSubH = Math.ceil(h * subH), pxSubX = Math.floor(subX * w), pxSubY = Math.floor(subY * h), pxTileW = Math.ceil(pxSubW / tilesX), pxTileH = Math.ceil(pxSubH / tilesY);
    for (let y = 0; y < tilesY; y++)
      for (let x = 0; x < tilesX; x++) {
        let ogRenderTarget = _renderer.getRenderTarget(), ogAutoClear = _renderer.autoClear, ogScissorTest = _renderer.getScissorTest();
        _renderer.getScissor(_ogScissor), _renderer.getViewport(_ogViewport);
        let tx = x, ty = y;
        if (!this.stableTiles) {
          let tileIndex = this._currentTile % (tilesX * tilesY);
          tx = tileIndex % tilesX, ty = ~~(tileIndex / tilesX), this._currentTile = tileIndex + 1;
        }
        let reverseTy = tilesY - ty - 1;
        _primaryTarget.scissor.set(
          pxSubX + tx * pxTileW,
          pxSubY + reverseTy * pxTileH,
          Math.min(pxTileW, pxSubW - tx * pxTileW),
          Math.min(pxTileH, pxSubH - reverseTy * pxTileH)
        ), _primaryTarget.viewport.set(
          pxSubX,
          pxSubY,
          pxSubW,
          pxSubH
        ), _renderer.setRenderTarget(_primaryTarget), _renderer.setScissorTest(!0), _renderer.autoClear = !1, _fsQuad.render(_renderer), _renderer.setViewport(_ogViewport), _renderer.setScissor(_ogScissor), _renderer.setScissorTest(ogScissorTest), _renderer.setRenderTarget(ogRenderTarget), _renderer.autoClear = ogAutoClear;
        const lastTile = x === tilesX - 1 && y === tilesY - 1;
        if (alpha && (lastTile || !this.completeSamplesOnly)) {
          blendMaterial.target1 = blendTarget1.texture;
          blendMaterial.target2 = _primaryTarget.texture;
          _renderer.setRenderTarget(blendTarget2);
          _blendQuad.render(_renderer);
          _renderer.setRenderTarget(ogRenderTarget);
          this._latestBlendTarget = blendTarget2;
        }
        this.samples += 1 / totalTiles;
        if (lastTile) this.samples = Math.round(this.samples);
        yield;
      }
    [blendTarget1, blendTarget2] = [blendTarget2, blendTarget1];
  }
}
var ogClearColor = new Color2(), PathTracingRenderer = class {
  get material() {
    return this._fsQuad.material;
  }
  set material(v2) {
    this._fsQuad.material.removeEventListener("recompilation", this._compileFunction), v2.addEventListener("recompilation", this._compileFunction), this._fsQuad.material = v2;
  }
  get target() {
    return this._alpha ? (this._latestBlendTarget || this._blendTargets[1]) : this._primaryTarget;
  }
  set alpha(v2) {
    this._alpha !== v2 && (v2 || (this._blendTargets[0].dispose(), this._blendTargets[1].dispose()), this._alpha = v2, this.reset());
  }
  get alpha() {
    return this._alpha;
  }
  get isCompiling() {
    return !!this._compilePromise;
  }
  constructor(renderer) {
    this.camera = null, this.tiles = new Vector27(3, 3), this.stableNoise = !1, this.stableTiles = !0, this.samples = 0, this._subframe = new Vector42(0, 0, 1, 1), this._opacityFactor = 1, this._renderer = renderer, this._alpha = !1, this._fsQuad = new FullScreenQuad(new PhysicalPathTracingMaterial()), this._blendQuad = new FullScreenQuad(new BlendMaterial()), this._task = null, this._currentTile = 0, this._compilePromise = null, this._sobolTarget = new SobolNumberMapGenerator().generate(renderer), this._primaryTarget = new WebGLRenderTarget2(1, 1, {
      format: RGBAFormat11,
      type: FloatType10,
      magFilter: NearestFilter8,
      minFilter: NearestFilter8
    }), this._blendTargets = [
      new WebGLRenderTarget2(1, 1, {
        format: RGBAFormat11,
        type: FloatType10,
        magFilter: NearestFilter8,
        minFilter: NearestFilter8
      }),
      new WebGLRenderTarget2(1, 1, {
        format: RGBAFormat11,
        type: FloatType10,
        magFilter: NearestFilter8,
        minFilter: NearestFilter8
      })
    ], this._compileFunction = () => {
      let promise = this.compileMaterial(this._fsQuad._mesh);
      promise.then(() => {
        this._compilePromise === promise && (this._compilePromise = null);
      }), this._compilePromise = promise;
    }, this.material.addEventListener("recompilation", this._compileFunction);
  }
  compileMaterial() {
    return this._renderer.compileAsync(this._fsQuad._mesh);
  }
  setCamera(camera) {
    let { material } = this;
    material.cameraWorldMatrix.copy(camera.matrixWorld), material.invProjectionMatrix.copy(camera.projectionMatrixInverse), material.physicalCamera.updateFrom(camera);
    let cameraType = 0;
    camera.projectionMatrix.elements[15] > 0 && (cameraType = 1), camera.isEquirectCamera && (cameraType = 2), material.setDefine("CAMERA_TYPE", cameraType), this.camera = camera;
  }
  setSize(w, h) {
    w = Math.ceil(w), h = Math.ceil(h), !(this._primaryTarget.width === w && this._primaryTarget.height === h) && (this._primaryTarget.setSize(w, h), this._blendTargets[0].setSize(w, h), this._blendTargets[1].setSize(w, h), this.reset());
  }
  getSize(target2) {
    target2.x = this._primaryTarget.width, target2.y = this._primaryTarget.height;
  }
  dispose() {
    this._primaryTarget.dispose(), this._blendTargets[0].dispose(), this._blendTargets[1].dispose(), this._sobolTarget.dispose(), this._fsQuad.dispose(), this._blendQuad.dispose(), this._task = null;
  }
  reset() {
    let { _renderer, _primaryTarget, _blendTargets } = this, ogRenderTarget = _renderer.getRenderTarget(), ogClearAlpha = _renderer.getClearAlpha();
    _renderer.getClearColor(ogClearColor), _renderer.setRenderTarget(_primaryTarget), _renderer.setClearColor(0, 0), _renderer.clearColor(), _renderer.setRenderTarget(_blendTargets[0]), _renderer.setClearColor(0, 0), _renderer.clearColor(), _renderer.setRenderTarget(_blendTargets[1]), _renderer.setClearColor(0, 0), _renderer.clearColor(), _renderer.setClearColor(ogClearColor, ogClearAlpha), _renderer.setRenderTarget(ogRenderTarget), this.samples = 0, this._task = null, this.material.stratifiedTexture.stableNoise = this.stableNoise, this.stableNoise && (this.material.seed = 0, this.material.stratifiedTexture.reset());
  }
  update() {
    this.material.onBeforeRender(), !this.isCompiling && (this._task || (this._task = renderTask.call(this)), this._task.next());
  }
};

// npm/three-gpu-pathtracer/src/textures/GradientEquirectTexture.js
import { Color as Color4, Vector3 as Vector313 } from "../three/build/three.module.js";

// npm/three-gpu-pathtracer/src/textures/ProceduralEquirectTexture.js
import {
  ClampToEdgeWrapping as ClampToEdgeWrapping5,
  Color as Color3,
  DataTexture as DataTexture8,
  DataUtils as DataUtils3,
  EquirectangularReflectionMapping,
  HalfFloatType as HalfFloatType3,
  LinearFilter as LinearFilter3,
  RepeatWrapping as RepeatWrapping3,
  RGBAFormat as RGBAFormat12,
  Spherical,
  Vector2 as Vector28
} from "../three/build/three.module.js";
var _uv = new Vector28(), _coord = new Vector28(), _polar = new Spherical(), _color = new Color3(), ProceduralEquirectTexture = class extends DataTexture8 {
  constructor(width = 512, height = 512) {
    super(
      new Uint16Array(width * height * 4),
      width,
      height,
      RGBAFormat12,
      HalfFloatType3,
      EquirectangularReflectionMapping,
      RepeatWrapping3,
      ClampToEdgeWrapping5,
      LinearFilter3,
      LinearFilter3
    ), this.generationCallback = null;
  }
  update() {
    this.dispose(), this.needsUpdate = !0;
    let { data, width, height } = this.image;
    for (let x = 0; x < width; x++)
      for (let y = 0; y < height; y++) {
        _coord.set(width, height), _uv.set(x / width, y / height), _uv.x -= 0.5, _uv.y = 1 - _uv.y, _polar.theta = _uv.x * 2 * Math.PI, _polar.phi = _uv.y * Math.PI, _polar.radius = 1, this.generationCallback(_polar, _uv, _coord, _color);
        let i4 = 4 * (y * width + x);
        data[i4 + 0] = DataUtils3.toHalfFloat(_color.r), data[i4 + 1] = DataUtils3.toHalfFloat(_color.g), data[i4 + 2] = DataUtils3.toHalfFloat(_color.b), data[i4 + 3] = DataUtils3.toHalfFloat(1);
      }
  }
  copy(other) {
    return super.copy(other), this.generationCallback = other.generationCallback, this;
  }
};

// npm/three-gpu-pathtracer/src/textures/GradientEquirectTexture.js
var _direction2 = new Vector313(), GradientEquirectTexture = class extends ProceduralEquirectTexture {
  constructor(resolution = 512) {
    super(resolution, resolution), this.topColor = new Color4().set(16777215), this.bottomColor = new Color4().set(0), this.exponent = 2, this.generationCallback = (polar, uv, coord, color) => {
      _direction2.setFromSpherical(polar);
      let t = _direction2.y * 0.5 + 0.5;
      color.lerpColors(this.bottomColor, this.topColor, t ** this.exponent);
    };
  }
  copy(other) {
    return super.copy(other), this.topColor.copy(other.topColor), this.bottomColor.copy(other.bottomColor), this;
  }
};

// npm/three-gpu-pathtracer/src/materials/fullscreen/ClampedInterpolationMaterial.js
import { ShaderMaterial as ShaderMaterial3 } from "../three/build/three.module.js";
var ClampedInterpolationMaterial = class extends ShaderMaterial3 {
  get map() {
    return this.uniforms.map.value;
  }
  set map(v2) {
    this.uniforms.map.value = v2;
  }
  get opacity() {
    return this.uniforms.opacity.value;
  }
  set opacity(v2) {
    this.uniforms && (this.uniforms.opacity.value = v2);
  }
  constructor(params) {
    super({
      uniforms: {
        map: { value: null },
        opacity: { value: 1 }
      },
      vertexShader: (
        /* glsl */
        `
				varying vec2 vUv;
				void main() {

					vUv = uv;
					gl_Position = projectionMatrix * modelViewMatrix * vec4( position, 1.0 );

				}
			`
      ),
      fragmentShader: (
        /* glsl */
        `
				uniform sampler2D map;
				uniform float opacity;
				varying vec2 vUv;

				vec4 clampedTexelFatch( sampler2D map, ivec2 px, int lod ) {

					vec4 res = texelFetch( map, ivec2( px.x, px.y ), 0 );

					#if defined( TONE_MAPPING )

					res.xyz = toneMapping( res.xyz );

					#endif

					return linearToOutputTexel( res );

				}

				void main() {

					vec2 size = vec2( textureSize( map, 0 ) );
					vec2 pxUv = vUv * size;
					vec2 pxCurr = floor( pxUv );
					vec2 pxFrac = fract( pxUv ) - 0.5;
					vec2 pxOffset;
					pxOffset.x = pxFrac.x > 0.0 ? 1.0 : - 1.0;
					pxOffset.y = pxFrac.y > 0.0 ? 1.0 : - 1.0;

					vec2 pxNext = clamp( pxOffset + pxCurr, vec2( 0.0 ), size - 1.0 );
					vec2 alpha = abs( pxFrac );

					vec4 p1 = mix(
						clampedTexelFatch( map, ivec2( pxCurr.x, pxCurr.y ), 0 ),
						clampedTexelFatch( map, ivec2( pxNext.x, pxCurr.y ), 0 ),
						alpha.x
					);

					vec4 p2 = mix(
						clampedTexelFatch( map, ivec2( pxCurr.x, pxNext.y ), 0 ),
						clampedTexelFatch( map, ivec2( pxNext.x, pxNext.y ), 0 ),
						alpha.x
					);

					gl_FragColor = mix( p1, p2, alpha.y );
					gl_FragColor.a *= opacity;
					#include <premultiplied_alpha_fragment>

				}
			`
      )
    }), this.setValues(params);
  }
};

// npm/three-gpu-pathtracer/src/utils/CubeToEquirectGenerator.js
import {
  DataTexture as DataTexture9,
  DataUtils as DataUtils4,
  EquirectangularReflectionMapping as EquirectangularReflectionMapping2,
  FloatType as FloatType11,
  HalfFloatType as HalfFloatType4,
  LinearFilter as LinearFilter4,
  LinearMipMapLinearFilter,
  RGBAFormat as RGBAFormat13,
  RepeatWrapping as RepeatWrapping4,
  ShaderMaterial as ShaderMaterial4,
  WebGLRenderTarget as WebGLRenderTarget3
} from "../three/build/three.module.js";
var CubeToEquirectMaterial = class extends ShaderMaterial4 {
  constructor() {
    super({
      uniforms: {
        envMap: { value: null },
        flipEnvMap: { value: -1 }
      },
      vertexShader: (
        /* glsl */
        `
				varying vec2 vUv;
				void main() {

					vUv = uv;
					gl_Position = projectionMatrix * modelViewMatrix * vec4( position, 1.0 );

				}`
      ),
      fragmentShader: (
        /* glsl */
        `
				#define ENVMAP_TYPE_CUBE_UV

				uniform samplerCube envMap;
				uniform float flipEnvMap;
				varying vec2 vUv;

				#include <common>
				#include <cube_uv_reflection_fragment>

				${util_functions}

				void main() {

					vec3 rayDirection = equirectUvToDirection( vUv );
					rayDirection.x *= flipEnvMap;
					gl_FragColor = textureCube( envMap, rayDirection );

				}`
      )
    }), this.depthWrite = !1, this.depthTest = !1;
  }
}, CubeToEquirectGenerator = class {
  constructor(renderer) {
    this._renderer = renderer, this._quad = new FullScreenQuad(new CubeToEquirectMaterial());
  }
  generate(source, width = null, height = null) {
    if (!source.isCubeTexture)
      throw new Error("CubeToEquirectMaterial: Source can only be cube textures.");
    let image = source.images[0], renderer = this._renderer, quad = this._quad;
    width === null && (width = 4 * image.height), height === null && (height = 2 * image.height);
    let target2 = new WebGLRenderTarget3(width, height, {
      type: FloatType11,
      colorSpace: image.colorSpace
    }), imageHeight = image.height, maxMip = Math.log2(imageHeight) - 2, texelHeight = 1 / imageHeight, texelWidth = 1 / (3 * Math.max(Math.pow(2, maxMip), 112));
    quad.material.defines.CUBEUV_MAX_MIP = `${maxMip}.0`, quad.material.defines.CUBEUV_TEXEL_WIDTH = texelWidth, quad.material.defines.CUBEUV_TEXEL_HEIGHT = texelHeight, quad.material.uniforms.envMap.value = source, quad.material.uniforms.flipEnvMap.value = source.isRenderTargetTexture ? 1 : -1, quad.material.needsUpdate = !0;
    let currentTarget = renderer.getRenderTarget(), currentAutoClear = renderer.autoClear;
    renderer.autoClear = !0, renderer.setRenderTarget(target2), quad.render(renderer), renderer.setRenderTarget(currentTarget), renderer.autoClear = currentAutoClear;
    let buffer = new Uint16Array(width * height * 4), readBuffer = new Float32Array(width * height * 4);
    renderer.readRenderTargetPixels(target2, 0, 0, width, height, readBuffer), target2.dispose();
    for (let i = 0, l = readBuffer.length; i < l; i++)
      buffer[i] = DataUtils4.toHalfFloat(readBuffer[i]);
    let result = new DataTexture9(buffer, width, height, RGBAFormat13, HalfFloatType4);
    return result.minFilter = LinearMipMapLinearFilter, result.magFilter = LinearFilter4, result.wrapS = RepeatWrapping4, result.wrapT = RepeatWrapping4, result.mapping = EquirectangularReflectionMapping2, result.needsUpdate = !0, result;
  }
  dispose() {
    this._quad.dispose();
  }
};

// npm/three-gpu-pathtracer/src/core/WebGLPathTracer.js
function supportsFloatBlending(renderer) {
  return renderer.extensions.get("EXT_float_blend");
}
var _resolution = new Vector29(), WebGLPathTracer = class {
  get multipleImportanceSampling() {
    return !!this._pathTracer.material.defines.FEATURE_MIS;
  }
  set multipleImportanceSampling(v2) {
    this._pathTracer.material.setDefine("FEATURE_MIS", v2 ? 1 : 0);
  }
  get transmissiveBounces() {
    return this._pathTracer.material.transmissiveBounces;
  }
  set transmissiveBounces(v2) {
    this._pathTracer.material.transmissiveBounces = v2;
  }
  get bounces() {
    return this._pathTracer.material.bounces;
  }
  set bounces(v2) {
    this._pathTracer.material.bounces = v2;
  }
  get filterGlossyFactor() {
    return this._pathTracer.material.filterGlossyFactor;
  }
  set filterGlossyFactor(v2) {
    this._pathTracer.material.filterGlossyFactor = v2;
  }
  get samples() {
    return this._pathTracer.samples;
  }
  get target() {
    return this._pathTracer.target;
  }
  get tiles() {
    return this._pathTracer.tiles;
  }
  get stableNoise() {
    return this._pathTracer.stableNoise;
  }
  set stableNoise(v2) {
    this._pathTracer.stableNoise = v2;
  }
  get isCompiling() {
    return !!this._pathTracer.isCompiling;
  }
  constructor(renderer) {
    console.warn('WebGLPathTracer: This class has been deprecated and will be removed in a future release. Use "WebGPUPathTracer" instead.'), this._renderer = renderer, this._generator = new PathTracingSceneGenerator(), this._pathTracer = new PathTracingRenderer(renderer), this._queueReset = !1, this._clock = new Clock(), this._compilePromise = null, this._lowResPathTracer = new PathTracingRenderer(renderer), this._lowResPathTracer.tiles.set(1, 1), this._quad = new FullScreenQuad(new ClampedInterpolationMaterial({
      map: null,
      transparent: !0,
      blending: NoBlending4,
      premultipliedAlpha: renderer.getContextAttributes().premultipliedAlpha
    })), this._materials = null, this._previousEnvironment = null, this._previousBackground = null, this._internalBackground = null, this.renderDelay = 100, this.minSamples = 5, this.fadeDuration = 500, this.enablePathTracing = !0, this.pausePathTracing = !1, this.dynamicLowRes = !1, this.lowResScale = 0.25, this.renderScale = 1, this.synchronizeRenderSize = !0, this.rasterizeScene = !0, this.renderToCanvas = !0, this.textureSize = new Vector29(1024, 1024), this.rasterizeSceneCallback = (scene, camera) => {
      this._renderer.render(scene, camera);
    }, this.renderToCanvasCallback = (target2, renderer2, quad) => {
      let currentAutoClear = renderer2.autoClear;
      renderer2.autoClear = !1, quad.render(renderer2), renderer2.autoClear = currentAutoClear;
    }, this.setScene(new Scene(), new PerspectiveCamera2());
  }
  setBVHWorker(worker) {
    this._generator.setBVHWorker(worker);
  }
  setScene(scene, camera, options = {}) {
    scene.updateMatrixWorld(!0), camera.updateMatrixWorld();
    let generator = this._generator;
    if (generator.setObjects(scene), this._buildAsync)
      return generator.generateAsync(options.onProgress).then((result) => this._updateFromResults(scene, camera, result));
    {
      let result = generator.generate();
      return this._updateFromResults(scene, camera, result);
    }
  }
  setSceneAsync(...args) {
    this._buildAsync = !0;
    let result = this.setScene(...args);
    return this._buildAsync = !1, result;
  }
  setCamera(camera) {
    this.camera = camera, this.updateCamera();
  }
  updateCamera() {
    let camera = this.camera;
    camera.updateMatrixWorld(), this._pathTracer.setCamera(camera), this._lowResPathTracer.setCamera(camera), this.reset();
  }
  updateMaterials() {
    let material = this._pathTracer.material, renderer = this._renderer, materials = this._materials, textureSize = this.textureSize, textures = getTextures2(materials);
    material.textures.setTextures(renderer, textures, textureSize.x, textureSize.y), material.materials.updateFrom(materials, textures), this.reset();
  }
  updateLights() {
    let scene = this.scene, renderer = this._renderer, material = this._pathTracer.material, lights = getLights2(scene), iesTextures = getIesTextures(lights);
    material.lights.updateFrom(lights, iesTextures), material.iesProfiles.setTextures(renderer, iesTextures), this.reset();
  }
  updateEnvironment() {
    let scene = this.scene, material = this._pathTracer.material;
    if (this._internalBackground && (this._internalBackground.dispose(), this._internalBackground = null), material.backgroundBlur = scene.backgroundBlurriness, material.backgroundIntensity = scene.backgroundIntensity ?? 1, material.backgroundRotation.makeRotationFromEuler(scene.backgroundRotation).invert(), scene.background === null)
      material.backgroundMap = null, material.backgroundAlpha = 0;
    else if (scene.background.isColor) {
      this._colorBackground = this._colorBackground || new GradientEquirectTexture(16);
      let colorBackground = this._colorBackground;
      colorBackground.topColor.equals(scene.background) || (colorBackground.topColor.set(scene.background), colorBackground.bottomColor.set(scene.background), colorBackground.update()), material.backgroundMap = colorBackground, material.backgroundAlpha = 1;
    } else if (scene.background.isCubeTexture) {
      if (scene.background !== this._previousBackground) {
        let background = new CubeToEquirectGenerator(this._renderer).generate(scene.background);
        this._internalBackground = background, material.backgroundMap = background, material.backgroundAlpha = 1;
      }
    } else
      material.backgroundMap = scene.background, material.backgroundAlpha = 1;
    if (material.environmentIntensity = scene.environment !== null ? scene.environmentIntensity ?? 1 : 0, material.environmentRotation.makeRotationFromEuler(scene.environmentRotation).invert(), this._previousEnvironment !== scene.environment && scene.environment !== null)
      if (scene.environment.isCubeTexture) {
        let environment = new CubeToEquirectGenerator(this._renderer).generate(scene.environment);
        material.envMapInfo.updateFrom(environment);
      } else
        material.envMapInfo.updateFrom(scene.environment);
    this._previousEnvironment = scene.environment, this._previousBackground = scene.background, this.reset();
  }
  _updateFromResults(scene, camera, results) {
    let {
      materials,
      geometry,
      bvh,
      bvhChanged,
      needsMaterialIndexUpdate
    } = results;
    this._materials = materials;
    let material = this._pathTracer.material;
    const extraUvs = this._generator.attributes.includes("uv1")
      ? ["uv1","uv2","uv3"].filter(key=>this._generator.attributes.includes(key)).map(key=>geometry.attributes[key])
      : [];
    material.setDefine("TRACE_UV_COUNT", 1 + extraUvs.length);
    material.setDefine("FEATURE_WUYING_WOOD", materials.some(m=>m.userData.wuyingWood)?1:0);
    return bvhChanged && (material.bvh.updateFrom(bvh), material.attributesArray.updateFrom(
      geometry.attributes.normal,
      geometry.attributes.tangent,
      geometry.attributes.uv,
      geometry.attributes.color, extraUvs
    )), needsMaterialIndexUpdate && material.materialIndexAttribute.updateFrom(geometry.attributes.materialIndex), this._previousScene = scene, this.scene = scene, this.camera = camera, this.updateCamera(), this.updateMaterials(), this.updateEnvironment(), this.updateLights(), results;
  }
  renderSample() {
    let lowResPathTracer = this._lowResPathTracer, pathTracer = this._pathTracer, renderer = this._renderer, clock = this._clock, quad = this._quad;
    this._updateScale(), this._queueReset && (pathTracer.reset(), lowResPathTracer.reset(), this._queueReset = !1, quad.material.opacity = 0, clock.start());
    let delta = clock.getDelta() * 1e3, elapsedTime = clock.getElapsedTime() * 1e3;
    if (!this.pausePathTracing && this.enablePathTracing && this.renderDelay <= elapsedTime && !this.isCompiling && pathTracer.update(), pathTracer.alpha = pathTracer.material.backgroundAlpha !== 1 || !supportsFloatBlending(renderer), lowResPathTracer.alpha = pathTracer.alpha, this.renderToCanvas) {
      let renderer2 = this._renderer, minSamples = this.minSamples;
      if (elapsedTime >= this.renderDelay && this.samples >= this.minSamples && (this.fadeDuration !== 0 ? quad.material.opacity = Math.min(quad.material.opacity + delta / this.fadeDuration, 1) : quad.material.opacity = 1), !this.enablePathTracing || this.samples < minSamples || quad.material.opacity < 1) {
        if (this.dynamicLowRes && !this.isCompiling) {
          lowResPathTracer.samples < 1 && (lowResPathTracer.material = pathTracer.material, lowResPathTracer.update());
          let currentOpacity = quad.material.opacity;
          quad.material.opacity = 1 - quad.material.opacity, quad.material.map = lowResPathTracer.target.texture, quad.render(renderer2), quad.material.opacity = currentOpacity;
        }
        (!this.dynamicLowRes && this.rasterizeScene || this.dynamicLowRes && this.isCompiling) && this.rasterizeSceneCallback(this.scene, this.camera);
      }
      this.enablePathTracing && quad.material.opacity > 0 && (quad.material.opacity < 1 && (quad.material.blending = this.dynamicLowRes ? AdditiveBlending : NormalBlending2), quad.material.map = pathTracer.target.texture, this.renderToCanvasCallback(pathTracer.target, renderer2, quad), quad.material.blending = NoBlending4);
    }
  }
  reset() {
    this._queueReset = !0, this._pathTracer.samples = 0;
  }
  dispose() {
    this._quad.dispose(), this._quad.material.dispose(), this._pathTracer.dispose();
  }
  _updateScale() {
    if (this.synchronizeRenderSize) {
      this._renderer.getDrawingBufferSize(_resolution);
      let w = Math.floor(this.renderScale * _resolution.x), h = Math.floor(this.renderScale * _resolution.y);
      if (this._pathTracer.getSize(_resolution), _resolution.x !== w || _resolution.y !== h) {
        let lowResScale = this.lowResScale;
        this._pathTracer.setSize(w, h), this._lowResPathTracer.setSize(Math.floor(w * lowResScale), Math.floor(h * lowResScale));
      }
    }
  }
  getRenderTime() {
    return this._clock.getElapsedTime() * 1e3;
  }
};
export {
  WebGLPathTracer
};
