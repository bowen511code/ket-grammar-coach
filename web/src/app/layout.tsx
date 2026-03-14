import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "KET Grammar Coach Demo",
  description:
    "AI grammar practice demo for KET learners, featuring student and teacher workflows.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body className="antialiased bg-white text-black">{children}</body>
    </html>
  );
}
