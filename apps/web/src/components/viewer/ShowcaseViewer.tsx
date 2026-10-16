"use client";

import { Bounds, Center, OrbitControls, useGLTF } from "@react-three/drei";
import { Canvas } from "@react-three/fiber";
import { Suspense, useEffect, useState } from "react";

function Model({ url }: { url: string }) {
  // Kernel GLBs are uncompressed: disable the Draco (remote CDN) and meshopt (WASM) decoders so nothing is fetched
  // from third parties and the CSP needs no wasm/eval allowance.
  const g = useGLTF(url, false, false);
  // Kernel output is Z-up in metres; rotate to the viewer's Y-up frame.
  return <primitive object={g.scene} rotation={[-Math.PI / 2, 0, 0]} />;
}

export function ShowcaseViewer({ url, label }: { url: string; label: string }) {
  const [ok, setOk] = useState<boolean | null>(null);
  useEffect(() => {
    try { const c = document.createElement("canvas"); setOk(!!(c.getContext("webgl2") || c.getContext("webgl"))); } catch { setOk(false); }
  }, []);
  if (ok === false) return <div className="empty" style={{ height: "100%" }}>3D preview needs WebGL. {label}</div>;
  if (ok === null) return null;
  return (
    <Canvas camera={{ position: [0.35, 0.25, 0.35], fov: 35, near: 0.001, far: 50 }} dpr={[1, 2]} aria-label={label} role="img">
      <color attach="background" args={["#0c1015"]} />
      <hemisphereLight args={["#dfe9f5", "#20262e", 0.9]} />
      <directionalLight position={[2, 3, 2]} intensity={1.8} />
      <directionalLight position={[-2, -1, -2]} intensity={0.5} />
      <Suspense fallback={null}>
        <Bounds fit clip observe margin={1.15} key={url}>
          <Center><Model url={url} /></Center>
        </Bounds>
      </Suspense>
      <OrbitControls makeDefault autoRotate autoRotateSpeed={0.8} enablePan={false} />
    </Canvas>
  );
}
