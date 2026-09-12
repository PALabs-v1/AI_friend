"use client"

import { useState, useEffect } from "react"

const STEPS = [
  {
    num: "01",
    title: "Clone & start",
    desc: "One command, refuses to half-boot",
    file: "terminal",
    lang: "bash",
    code: [
      { type: "comment", text: "# Clone and configure" },
      { type: "command", text: "git clone https://github.com/PALabs-v1/AI_friend.git" },
      { type: "command", text: "cd AI_friend && cp .env.example .env" },
      { type: "gap" },
      { type: "comment", text: "# Network, Ollama, default voice, schema, mesh — all of it" },
      { type: "command", text: "./start.sh" },
      { type: "gap" },
      { type: "output", text: "==> Starting the mesh..." },
      { type: "success", text: "==> Done." },
    ],
  },
  {
    num: "02",
    title: "Describe your friend",
    desc: "Freeform prose, not a template",
    file: "terminal",
    lang: "bash",
    code: [
      { type: "comment", text: "# The interactive companion CLI wizard" },
      { type: "command", text: "cd backend" },
      { type: "command", text: "../.venv/bin/python -m scripts.create_friend" },
      { type: "gap" },
      { type: "output", text: "> She's blunt, hates small talk, gets genuinely" },
      { type: "output", text: "  annoyed when I dodge a question." },
      { type: "gap" },
      { type: "success", text: "✓ compiled — preview before you commit to anything" },
    ],
  },
  {
    num: "03",
    title: "Give them a voice",
    desc: "8 seconds, transcribed automatically",
    file: "terminal",
    lang: "bash",
    code: [
      { type: "comment", text: "# Or skip this — a bundled default voice speaks first" },
      { type: "command", text: "../.venv/bin/python backend/scripts/audio/record_voice.py --duration 8" },
      { type: "gap" },
      { type: "output", text: "  Recording... done." },
      { type: "output", text: "  Transcribing with whisper.cpp..." },
      { type: "output", text: "  Validating clip: duration, loudness, clipping..." },
      { type: "success", text: "✓ saved as REF_AUDIO_PATH / REF_TEXT" },
    ],
  },
  {
    num: "04",
    title: "Talk",
    desc: "Text first, voice once you're ready",
    file: "terminal",
    lang: "bash",
    code: [
      { type: "comment", text: "# The real cognitive pipeline — memory, affect, everything" },
      { type: "command", text: "../.venv/bin/python -m scripts.talk" },
      { type: "gap" },
      { type: "output", text: "> hey, long day" },
      { type: "gap" },
      { type: "plain", text: "friend: yeah? what happened" },
    ],
  },
]

function CodeLine({ line }: { line: (typeof STEPS)[0]["code"][0] }) {
  if (line.type === "gap") return <div className="h-3" />
  if (line.type === "comment") return <div className="text-[#9ca3af]">{line.text}</div>
  if (line.type === "output") return <div className="text-[#6b7280]">{line.text}</div>
  if (line.type === "success") return <div className="text-[#16a34a]">{line.text}</div>
  if (line.type === "url") return <div className="text-[#2563eb] underline">{line.text}</div>
  if (line.type === "command") return (
    <div>
      <span className="text-[#16a34a]">$ </span>
      <span className="text-[#111]">{line.text}</span>
    </div>
  )
  if (line.type === "plain") return <div className="text-[#111]">{line.text}</div>
  return null
}

export function DevExSection() {
  const [active, setActive] = useState(0)
  const [visible, setVisible] = useState(true)

  function selectStep(i: number) {
    if (i === active) return
    setVisible(false)
    setTimeout(() => {
      setActive(i)
      setVisible(true)
    }, 180)
  }

  // Auto-advance every 3s
  useEffect(() => {
    const t = setInterval(() => {
      setVisible(false)
      setTimeout(() => {
        setActive(prev => (prev + 1) % STEPS.length)
        setVisible(true)
      }, 180)
    }, 3200)
    return () => clearInterval(t)
  }, [])

  const step = STEPS[active]

  return (
    <section id="setup" className="py-32 px-6 md:px-12 lg:px-20 border-t border-black/[0.06]">
      <div className="max-w-6xl mx-auto">
        <div className="mb-16">
          <div className="mt-4 inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-black/[0.05] border border-black/[0.06] text-[10px] tracking-widest text-black/40 uppercase">
            Get started
          </div>
          <h2 className="mt-5 text-4xl md:text-5xl font-light tracking-tight leading-[1.05]">
            Four commands.<br />That's the whole flow.
          </h2>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-3 items-stretch">
          {/* Left — 4 clickable step cards, equal height, no flex stretch */}
          <div className="flex flex-col gap-3">
            {STEPS.map((s, i) => (
              <button
                key={s.num}
                onClick={() => selectStep(i)}
                className="flex-1 text-left rounded-2xl border transition-all duration-200 p-6 group"
                style={{
                  background: active === i ? "rgba(0,0,0,0.04)" : "rgba(255,255,255,0.7)",
                  borderColor: active === i ? "rgba(0,0,0,0.12)" : "rgba(0,0,0,0.06)",
                  boxShadow: active === i
                    ? "0 1px 3px rgba(0,0,0,0.06)"
                    : "0 1px 2px rgba(0,0,0,0.03)",
                }}
              >
                <div className="flex gap-4 items-start">
                  <div
                    className="flex items-center justify-center w-8 h-8 rounded-lg text-xs font-light shrink-0 transition-colors duration-200"
                    style={{
                      background: active === i ? "rgba(0,0,0,0.08)" : "rgba(0,0,0,0.04)",
                      color: active === i ? "rgba(0,0,0,0.7)" : "rgba(0,0,0,0.35)",
                    }}
                  >
                    {s.num}
                  </div>
                  <div className="min-w-0">
                    <p
                      className="text-sm font-light transition-colors duration-200"
                      style={{ color: active === i ? "rgba(0,0,0,0.8)" : "rgba(0,0,0,0.5)" }}
                    >
                      {s.title}
                    </p>
                    <p className="text-xs mt-0.5" style={{ color: "rgba(0,0,0,0.28)" }}>{s.desc}</p>
                  </div>
                </div>
              </button>
            ))}
          </div>

          {/* Right — fixed-size code panel */}
          <div
            className="lg:col-span-2 rounded-2xl border border-black/[0.06] p-8 flex flex-col"
            style={{
              background: "rgba(255,255,255,0.7)",
              boxShadow: "0 1px 3px rgba(0,0,0,0.04)",
              minHeight: "360px",
            }}
          >
            {/* Header */}
            <div className="flex items-center justify-between mb-5 shrink-0">
              <div
                className="text-[10px] tracking-widest uppercase transition-all duration-200"
                style={{
                  opacity: visible ? 1 : 0,
                  filter: visible ? "blur(0px)" : "blur(4px)",
                  transition: "opacity 200ms ease, filter 200ms ease",
                  color: "rgba(0,0,0,0.3)",
                }}
              >
                {step.file}
              </div>
              <div className="flex gap-1.5">
                {[0, 1, 2].map(d => (
                  <div
                    key={d}
                    className="w-2 h-2 rounded-full transition-all duration-300"
                    style={{
                      background: d === active % 3 ? "rgba(0,0,0,0.25)" : "rgba(0,0,0,0.08)",
                    }}
                  />
                ))}
              </div>
            </div>

            {/* Code block — fixed height, content doesn't affect layout */}
            <div className="flex-1 rounded-xl p-6 overflow-hidden" style={{ background: "rgba(0,0,0,0.03)", border: "1px solid rgba(0,0,0,0.06)" }}>
              <div
                className="font-mono text-[12px] leading-6"
                style={{
                  opacity: visible ? 1 : 0,
                  filter: visible ? "blur(0px)" : "blur(6px)",
                  transform: visible ? "translateY(0)" : "translateY(6px)",
                  transition: "opacity 220ms cubic-bezier(0.16,1,0.3,1), filter 220ms cubic-bezier(0.16,1,0.3,1), transform 220ms cubic-bezier(0.16,1,0.3,1)",
                }}
              >
                {step.code.map((line, i) => (
                  <CodeLine key={i} line={line} />
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
