import type { Metadata, Viewport } from "next";
import Script from "next/script";

import "./globals.css";

export const metadata: Metadata = {
  title: "QuoteHawk | RFQ approval workspace",
  description: "Draft-only RFQ comparison and buyer approval workspace for QuoteHawk.",
  applicationName: "QuoteHawk",
  other: {
    "page-type": "software",
    "site-kind": "software",
  },
};

export const viewport: Viewport = {
  colorScheme: "dark",
  themeColor: "#0c0a08",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" data-type="software" data-radius="soft" data-buttons="solid" data-motion="on">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Geist+Mono:wght@400;500&family=Mona+Sans:wdth,wght@75..125,400..600&display=swap"
          rel="stylesheet"
        />
        <link href="/taste/moments/iris-opens/moment.css" rel="stylesheet" />
        <script
          dangerouslySetInnerHTML={{
            __html:
              "document.documentElement.classList.add('mo-js');setTimeout(()=>document.documentElement.classList.add('mo-safe'),2500);",
          }}
        />
        <link rel="modulepreload" href="/taste/moments/moments.js" />
        <link rel="modulepreload" href="/taste/moments/lib.js" />
        <link rel="modulepreload" href="/taste/moments/iris-opens/moment.js" />
      </head>
      <body>
        <Script id="hero-enter" strategy="beforeInteractive">
          {"document.documentElement.classList.add('m-enter');setTimeout(()=>document.documentElement.classList.add('m-enter-done'),700);"}
        </Script>
        <Script type="module" src="/taste/moments/moments.js" strategy="afterInteractive" />
        {children}
      </body>
    </html>
  );
}
