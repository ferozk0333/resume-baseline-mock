import type { Metadata } from "next";
import { Suspense } from "react";
import { Inter } from "next/font/google";
import { Toaster } from "@/components/ui/sonner";
import { AppMain } from "@/components/shared/app-main";
import { ModeBanner } from "@/components/shared/mode-banner";
import { SiteHeader } from "@/components/shared/site-header";
import "./globals.css";

const inter = Inter({ variable: "--font-sans", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "RightStaff",
  description: "Score a profile against a role, see the gaps and how to close them.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${inter.variable} h-full antialiased`}>
      <body className="min-h-full flex flex-col">
        <Suspense fallback={<div className="h-14 border-b bg-white" />}><SiteHeader /></Suspense>
        <Suspense fallback={null}><ModeBanner /></Suspense>
        <Suspense fallback={<main className="mx-auto w-full max-w-6xl flex-1 px-6 py-8">{children}</main>}>
          <AppMain>{children}</AppMain>
        </Suspense>
        <Toaster position="bottom-right" theme="light" />
      </body>
    </html>
  );
}
