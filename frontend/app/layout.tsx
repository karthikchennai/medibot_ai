import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "MediBot · Hospital knowledge, connected",
  description: "A role-aware workspace for hospital knowledge and operational insights, powered by Groq.",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
