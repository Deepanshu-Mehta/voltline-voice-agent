"use client";

import { useEffect, useState } from "react";
import Chat from "@/components/Chat";
import VoiceAgent from "@/components/VoiceAgent";

// Voice is the default; a question passed as /talk?q=... opens the text tab instead.
export default function TalkPanel() {
  const [tab, setTab] = useState<"voice" | "text">("voice");

  useEffect(() => {
    if (!new URLSearchParams(window.location.search).get("q")) return;
    const t = setTimeout(() => setTab("text"), 0);
    return () => clearTimeout(t);
  }, []);

  return (
    <div className="talk-panel">
      <div className="segmented" role="tablist" aria-label="How to talk to Volt">
        <button type="button" role="tab" id="tab-voice" className="segment" aria-selected={tab === "voice"}
          aria-controls="panel-voice" onClick={() => setTab("voice")}>
          Voice
        </button>
        <button type="button" role="tab" id="tab-text" className="segment" aria-selected={tab === "text"}
          aria-controls="panel-text" onClick={() => setTab("text")}>
          Text
        </button>
      </div>
      <div role="tabpanel" id="panel-voice" aria-labelledby="tab-voice" hidden={tab !== "voice"}>
        <VoiceAgent />
      </div>
      <div role="tabpanel" id="panel-text" aria-labelledby="tab-text" hidden={tab !== "text"}>
        <Chat autoFromUrl />
      </div>
    </div>
  );
}
