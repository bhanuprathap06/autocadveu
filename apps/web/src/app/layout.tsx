import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import { Providers } from "@/lib/providers";
import "@/styles/globals.css";

export const metadata: Metadata = {
  title: { default: "AutoForge Engineering Studio", template: "%s · AutoForge" },
  description:
    "Browser-based automotive CAD, simulation and engineering platform: parametric OCCT modelling, assemblies, sourced component catalog, CalculiX simulation, drawings and private collaboration.",
};

export const viewport: Viewport = { themeColor: "#0a0c0f", colorScheme: "dark" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <a href="#main" className="skip-link">Skip to content</a>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
