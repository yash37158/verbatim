import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Verbatim — ask your documents",
  description:
    "Upload a document and ask questions in your own words. Every answer quotes the source, "
    + "and every quote is checked against the document before you see it.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="antialiased">{children}</body>
    </html>
  );
}
