import { useEffect, useRef } from "react";

type RotationLike = { x: number; y: number; z: number };
type PositionLike = { set(x: number, y: number, z: number): void };
type Object3DLike = {
  rotation: RotationLike;
  position: PositionLike;
  add(...objects: Object3DLike[]): void;
};
type CameraLike = Object3DLike & {
  aspect: number;
  updateProjectionMatrix(): void;
};
type RendererLike = {
  domElement: HTMLCanvasElement;
  setPixelRatio(value: number): void;
  setSize(width: number, height: number, updateStyle?: boolean): void;
  render(scene: Object3DLike, camera: CameraLike): void;
  dispose(): void;
};
type ThreeApi = {
  Scene: new () => Object3DLike;
  PerspectiveCamera: new (fov: number, aspect: number, near: number, far: number) => CameraLike;
  WebGLRenderer: new (options: { alpha: boolean; antialias: boolean }) => RendererLike;
  BoxGeometry: new (width: number, height: number, depth: number) => unknown;
  MeshNormalMaterial: new (options: { flatShading: boolean; transparent: boolean; opacity: number }) => unknown;
  Mesh: new (geometry: unknown, material: unknown) => Object3DLike;
};

type AttitudeSceneProps = {
  roll: number | null;
  pitch: number | null;
  yaw: number | null;
};

const radians = (degrees: number | null) => ((degrees ?? 0) * Math.PI) / 180;

export function AttitudeScene({ roll, pitch, yaw }: AttitudeSceneProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const meshRef = useRef<Object3DLike | null>(null);

  useEffect(() => {
    let disposed = false;
    let frame = 0;
    let renderer: RendererLike | null = null;
    let resizeObserver: ResizeObserver | null = null;
    const host = hostRef.current;
    if (!host) return;

    const initialize = async () => {
      const nativeImport = new Function("url", "return import(url)") as (url: string) => Promise<unknown>;
      const api = (await nativeImport("/xinsu/vendor/three.module.js")) as ThreeApi;
      if (disposed) return;
      const scene = new api.Scene();
      const camera = new api.PerspectiveCamera(38, 1, 0.1, 100);
      camera.position.set(0, 0.35, 5.4);
      renderer = new api.WebGLRenderer({ alpha: true, antialias: true });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      host.replaceChildren(renderer.domElement);

      const geometry = new api.BoxGeometry(2.5, 0.68, 1.08);
      const material = new api.MeshNormalMaterial({ flatShading: true, transparent: true, opacity: 0.88 });
      const mesh = new api.Mesh(geometry, material);
      mesh.rotation.x = radians(roll);
      mesh.rotation.y = radians(yaw);
      mesh.rotation.z = radians(pitch);
      meshRef.current = mesh;
      scene.add(mesh);

      const resize = () => {
        if (!renderer) return;
        const rect = host.getBoundingClientRect();
        const width = Math.max(1, rect.width);
        const height = Math.max(1, rect.height);
        renderer.setSize(width, height, false);
        camera.aspect = width / height;
        camera.updateProjectionMatrix();
      };
      resizeObserver = new ResizeObserver(resize);
      resizeObserver.observe(host);
      resize();

      const render = () => {
        if (disposed || !renderer) return;
        renderer.render(scene, camera);
        frame = window.requestAnimationFrame(render);
      };
      render();
    };

    void initialize();
    return () => {
      disposed = true;
      window.cancelAnimationFrame(frame);
      resizeObserver?.disconnect();
      renderer?.dispose();
      meshRef.current = null;
      host.replaceChildren();
    };
  }, []);

  useEffect(() => {
    const mesh = meshRef.current;
    if (!mesh) return;
    mesh.rotation.x = radians(roll);
    mesh.rotation.y = radians(yaw);
    mesh.rotation.z = radians(pitch);
  }, [pitch, roll, yaw]);

  return <div className="attitude-scene" ref={hostRef} aria-label="IMU 三维姿态" />;
}
