/**
 * Where each sensor sits on the gantry: one sensor in the middle; with more, half on the left (angle pi) and
 * the rest on the right (angle 0), outer positions first.
 */
export function trackFor(index: number, count: number): { angle: number; radius: number } {
  const angle = count > 1 && index < count / 2 ? Math.PI : 0;
  const radius = count === 1 ? 0 : 0.82 - (0.5 * index) / (count - 1);
  return { angle, radius };
}
