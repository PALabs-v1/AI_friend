import React from "react"
import type { Metadata } from 'next'
import { Analytics } from '@vercel/analytics/next'
import { SpeedInsights } from '@vercel/speed-insights/next'
import './globals.css'

export const metadata: Metadata = {
  title: 'PALabs — A friend of your own making',
  description: 'An AI friend you describe in your own words, that speaks in a voice you gave it, runs entirely on your own machine, and remembers who you are. Local-first, open source, MIT licensed.',
  keywords: ['AI companion', 'local AI', 'self-hosted AI', 'voice cloning', 'open source'],
  authors: [{ name: 'PALabs contributors' }],
  openGraph: {
    title: 'PALabs — A friend of your own making',
    description: 'Describe them in your own words. They speak in a voice you gave them, remember who you are, and run entirely on your own hardware.',
    type: 'website',
    url: 'https://github.com/PALabs-v1/AI_friend',
    siteName: 'PALabs',
  },
  twitter: {
    card: 'summary_large_image',
    title: 'PALabs — A friend of your own making',
    description: 'Describe them in your own words. They speak in a voice you gave them, remember who you are, and run entirely on your own hardware.',
  },
  icons: {
    icon: [
      {
        url: '/favicon.ico',
        sizes: 'any',
      },
      {
        url: '/icon.svg',
        type: 'image/svg+xml',
      },
      {
        url: '/icon-light-32x32.png',
        sizes: '32x32',
        media: '(prefers-color-scheme: light)',
      },
      {
        url: '/icon-dark-32x32.png',
        sizes: '32x32',
        media: '(prefers-color-scheme: dark)',
      },
    ],
    apple: [
      {
        url: '/apple-icon.png',
        sizes: '180x180',
        type: 'image/png',
      },
    ],
  },
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="en">
      <body className={`font-sans antialiased`}>
        {children}
        <Analytics />
        <SpeedInsights />
      </body>
    </html>
  )
}
