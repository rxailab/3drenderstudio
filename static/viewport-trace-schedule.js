export const fencePollMs = 2;

// A fence can finish anywhere between the last busy check and the next
// wakeup. A late OS timer must not permanently strand fast GPUs at one tile.
export function estimateTraceTileMs(previous, tiles, busyMs, elapsed) {
  const measured = Math.min(40, Math.min(elapsed, busyMs + fencePollMs) / tiles);
  // Shrink expensive batches immediately and grow cheap batches gradually.
  return Math.max(.2, measured, (previous + measured) / 2);
}

// Submit a short burst of tiles, then wait for its GPU fence. End each
// burst at a sample edge so completed frames can still be presented.
export function traceTileBatch(samples, tilesPerSample, tileMs) {
  const tilesLeft = tilesPerSample - Math.round(samples * tilesPerSample) % tilesPerSample;
  return Math.min(tilesLeft, 8, Math.max(1, Math.floor(10 / tileMs)));
}

export class TraceDelivery {
  constructor(limit) {
    this.limit = limit;
    this.sent = 0;
    this.pending = 0;
    this.sentAt = 0;
  }

  due(samples, now) {
    if (this.pending || !Number.isInteger(samples) || samples <= this.sent) return false;
    if (this.sent === 0 || samples === this.limit) return true;
    const stride = samples <= 8 ? 4 : 8;
    const elapsed = now - this.sentAt;
    return (samples % stride === 0 && elapsed >= 160) || elapsed >= 900;
  }

  submitted(samples, now) {
    this.pending = this.sent = samples;
    this.sentAt = now;
  }

  acknowledge(samples) {
    if (samples === this.pending) this.pending = 0;
  }
}
