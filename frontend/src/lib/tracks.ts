/**
 * Where each sensor's laser meets the turning part: the same rule as the
 * backend (engine.track_angle), so the twin paints where the data comes from.
 * Half the sensors sit on the left (angle pi), the rest on the right (angle 0),
 * outer tracks first.
 */
export function trackFor(index: number, count: number): { angle: number; radius: number } {
  const angle = count > 1 && index < count / 2 ? Math.PI : 0;
  const radius = count === 1 ? 0.55 : 0.82 - (0.5 * index) / (count - 1);
  return { angle, radius };
}
