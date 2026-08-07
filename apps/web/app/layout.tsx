import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { Providers } from "./providers";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Lamoon HR",
  description: "AI-first HRMS + ATS",
};

/* Applied before first paint so the theme never flashes. Reads an explicit
   choice if the user made one, otherwise follows the OS. Kept inline (rather
   than in a component) precisely because it must run before React hydrates. */
const THEME_BOOTSTRAP = `
(function(){
  try {
    var saved = localStorage.getItem('lamoon_theme');
    var dark = saved ? saved === 'dark'
                     : window.matchMedia('(prefers-color-scheme: dark)').matches;
    if (dark) document.documentElement.classList.add('dark');
  } catch (e) {}
})();
`;

// Typed explicitly rather than via Next's generated `LayoutProps` global, so
// `tsc --noEmit` works on a clean checkout without a build having run first.
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOTSTRAP }} />
      </head>
      <body className="min-h-full flex flex-col">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
