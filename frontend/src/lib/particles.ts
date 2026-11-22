// "Dismiss" effect: the chart breaks into glowing dust that blows away.

interface Particle {
  x: number;
  y: number;
  vx: number;
  vy: number;
  color: string;
  delay: number;
}

/**
 * Samples the visible pixels of `source` (a canvas drawn by ECharts) and
 * animates them on `overlay`, which must cover `stage`. Resolves when done.
 */
export function dissolve(source: HTMLCanvasElement, sourceRect: DOMRect, overlay: HTMLCanvasElement,
                         stageRect: DOMRect): Promise<void> {
  return new Promise((resolve) => {
    const ctx = source.getContext("2d");
    if (!ctx || source.width === 0 || source.height === 0) {
      resolve();
      return;
    }
    const dpr = window.devicePixelRatio || 1;
    const { width: W, height: H } = source;
    const pixels = ctx.getImageData(0, 0, W, H).data;
    const step = Math.max(2, Math.round(3 * dpr));
    const sx = sourceRect.width / W;
    const sy = sourceRect.height / H;
    const particles: Particle[] = [];
    for (let y = 0; y < H; y += step) {
      for (let x = 0; x < W; x += step) {
        const k = (y * W + x) * 4;
        if (pixels[k + 3] < 60) continue;
        particles.push({
          x: sourceRect.left - stageRect.left + x * sx,
          y: sourceRect.top - stageRect.top + y * sy,
          vx: Math.random() * 1.6 - 0.2,
          vy: -(Math.random() * 1.4 + 0.2),
          color: `rgba(${pixels[k]},${pixels[k + 1]},${pixels[k + 2]},`,
          delay: Math.random() * 0.3 + (x / W) * 0.65,
        });
      }
    }
    overlay.width = Math.round(stageRect.width * dpr);
    overlay.height = Math.round(stageRect.height * dpr);
    const g = overlay.getContext("2d");
    if (!g) {
      resolve();
      return;
    }
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.globalCompositeOperation = "lighter";
    const size = step * sx;
    const start = performance.now();
    const frame = (now: number) => {
      const t = (now - start) / 1000;
      g.clearRect(0, 0, stageRect.width, stageRect.height);
      let alive = 0;
      for (const p of particles) {
        const life = t - p.delay;
        let alpha = 1;
        let x = p.x;
        let y = p.y;
        let s = size;
        if (life > 0) {
          alpha = 1 - life / 0.95;
          if (alpha <= 0) continue;
          x += p.vx * life * 95 + life * life * 80;
          y += p.vy * life * 75;
          s = 1.6;
        }
        alive++;
        g.fillStyle = `${p.color}${alpha.toFixed(2)})`;
        g.fillRect(x, y, s, s);
      }
      if (alive) {
        requestAnimationFrame(frame);
      } else {
        g.clearRect(0, 0, stageRect.width, stageRect.height);
        resolve();
      }
    };
    requestAnimationFrame(frame);
  });
}
