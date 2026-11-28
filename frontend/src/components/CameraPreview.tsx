// The live picture of the camera next to the sensor; it flashes when a part is photographed.
import { useEffect, useRef } from "react";
import { controller } from "../controller";
import { camera } from "../lib/camera";
import { useStore } from "../state/store";
import { Icon } from "./Icon";

export function CameraPreview() {
  const cam = useStore((s) => s.camera);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const host = box.current;
    if (!cam.on || !host) return;
    host.prepend(camera.video);
    void camera.video.play().catch(() => undefined);
    return () => {
      if (camera.video.parentElement === host) host.removeChild(camera.video);
    };
  }, [cam.on]);

  if (!cam.on) return null;
  const flash = Date.now() - cam.snapAt < 900;
  return (
    <div className="camera-pip">
      <div className="camera-frame" ref={box}>
        {flash && <div className="camera-flash" key={cam.snapAt} />}
      </div>
      <div className="camera-bar">
        <span className="camera-dot" />
        <span className="camera-label">{cam.snapPart ? `Part ${cam.snapPart} photographed` : cam.label ?? "Camera"}</span>
        <button className="icon-only" title="Next camera" onClick={() => void controller.toggleCamera(true)}><Icon name="back" /></button>
        <button className="icon-only" title="Camera off" onClick={() => void controller.toggleCamera()}><Icon name="close" /></button>
      </div>
    </div>
  );
}
