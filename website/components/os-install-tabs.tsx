"use client"

import React, { useState, useEffect } from "react"

export function OSInstallTabs() {
  const [selectedOS, setSelectedOS] = useState<"mac" | "linux" | "windows" | "docker">("mac")
  const [copied, setCopied] = useState(false)

  // Client-side automatic OS detection
  useEffect(() => {
    if (typeof window === "undefined") return
    const ua = window.navigator.userAgent.toLowerCase()
    if (ua.includes("win")) {
      setSelectedOS("windows")
    } else if (ua.includes("linux")) {
      setSelectedOS("linux")
    } else if (ua.includes("mac")) {
      setSelectedOS("mac")
    }
  }, [])

  const installCommands = {
    mac: `curl -fsSL https://raw.githubusercontent.com/PALabs-v1/AI_friend/main/scripts/install.sh | bash`,
    linux: `curl -fsSL https://raw.githubusercontent.com/PALabs-v1/AI_friend/main/scripts/install.sh | bash`,
    windows: `irm https://raw.githubusercontent.com/PALabs-v1/AI_friend/main/scripts/install.ps1 | iex`,
    docker: `git clone https://github.com/PALabs-v1/AI_friend.git && cd AI_friend && ./start.sh`,
  }

  const activeCommand = installCommands[selectedOS]

  const handleCopy = () => {
    navigator.clipboard.writeText(activeCommand)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="rounded-2xl border border-black/[0.08] bg-white p-6 md:p-8 shadow-sm space-y-6">
      {/* OS Tab Selector */}
      <div className="flex flex-wrap items-center justify-between gap-4 border-b border-black/[0.06] pb-4">
        <div className="flex flex-wrap gap-1.5">
          {[
            { id: "mac", label: "macOS (Apple Silicon / Intel)", icon: "" },
            { id: "windows", label: "Windows 10/11 (PowerShell)", icon: "⊞" },
            { id: "linux", label: "Linux (Ubuntu / Arch / Fedora)", icon: "🐧" },
            { id: "docker", label: "Docker Compose (Direct)", icon: "🐳" },
          ].map((tab) => (
            <button
              key={tab.id}
              onClick={() => setSelectedOS(tab.id as any)}
              className={`px-3.5 py-2 rounded-xl text-xs transition-all flex items-center gap-1.5 ${
                selectedOS === tab.id
                  ? "bg-[#111] text-white shadow-xs font-medium"
                  : "bg-black/[0.04] text-black/60 hover:bg-black/[0.07]"
              }`}
            >
              <span>{tab.icon}</span>
              <span>{tab.label}</span>
            </button>
          ))}
        </div>

        <span className="text-[10px] font-mono text-emerald-800 bg-emerald-50 px-2.5 py-1 rounded-full border border-emerald-200">
          ● Auto-Detected Recommended
        </span>
      </div>

      {/* Terminal Code Copy Block */}
      <div className="rounded-xl border border-black/[0.08] bg-[#111] text-white p-4 font-mono text-xs shadow-inner space-y-3">
        <div className="flex items-center justify-between text-white/40 text-[10px] border-b border-white/10 pb-2">
          <span>{selectedOS === "windows" ? "PowerShell (Administrator)" : "Terminal (Bash/Zsh)"}</span>
          <button
            onClick={handleCopy}
            className="px-2.5 py-1 rounded bg-white/10 hover:bg-white/20 text-white transition-colors flex items-center gap-1 text-[11px]"
          >
            <span>{copied ? "✓ Copied" : "Copy Command"}</span>
          </button>
        </div>

        <div className="flex items-center gap-2 text-emerald-400 overflow-x-auto py-1">
          <span className="text-white/40 select-none">$</span>
          <code className="text-white selection:bg-white/20 whitespace-nowrap">
            {activeCommand}
          </code>
        </div>
      </div>

      {/* OS Specific Guidance & Post-Install */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 pt-2">
        <div className="p-3.5 rounded-xl bg-[#fafaf8] border border-black/[0.05] space-y-1">
          <span className="font-mono text-[10px] uppercase text-black/40 block font-semibold">1. Prerequisites</span>
          <p className="text-xs text-black/60 leading-relaxed">
            {selectedOS === "mac" && "Requires macOS 13+, Docker Desktop, and Python 3.11+."}
            {selectedOS === "windows" && "Requires Windows 10/11, Docker Desktop (WSL2), and PowerShell."}
            {selectedOS === "linux" && "Requires Linux x86_64 or aarch64, Docker Engine, and Python 3.11+."}
            {selectedOS === "docker" && "Runs all 9 agents inside Docker without local build toolchains."}
          </p>
        </div>

        <div className="p-3.5 rounded-xl bg-[#fafaf8] border border-black/[0.05] space-y-1">
          <span className="font-mono text-[10px] uppercase text-black/40 block font-semibold">2. Lightweight Runtime</span>
          <p className="text-xs text-black/60 leading-relaxed">
            Downloads only the compact 4.3 MB runtime package without cloning the full repository.
          </p>
        </div>

        <div className="p-3.5 rounded-xl bg-[#fafaf8] border border-black/[0.05] space-y-1">
          <span className="font-mono text-[10px] uppercase text-black/40 block font-semibold">3. Interactive Setup</span>
          <p className="text-xs text-black/60 leading-relaxed">
            Guided setup wizard configures your credentials and chosen local/cloud model interactively.
          </p>
        </div>
      </div>
    </div>
  )
}
