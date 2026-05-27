import os

pages = {
    "tournaments": {
        "title": "TOURNAMENT OPERATIONS CENTER",
        "subtitle": "Automated Room Setup & Brackets",
        "content": """
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mt-6">
          <div className="bg-black border border-white/10 rounded-xl p-6">
            <h3 className="font-mono font-bold text-sm text-slate-400 mb-4 border-b border-white/5 pb-2">ACTIVE TOURNAMENTS</h3>
            <div className="space-y-3">
              {["PMGC Finals 2026", "BGIS Quarter Finals", "Creator Showdown"].map(t => (
                <div key={t} className="flex justify-between items-center bg-white/5 p-3 rounded border border-white/10">
                  <span className="font-mono text-white text-sm">{t}</span>
                  <button onClick={() => send({ action: 'manage_tournament', t })} className="text-[10px] bg-[#00f0ff]/20 hover:bg-[#00f0ff]/40 font-bold text-[#00f0ff] px-2 py-1 rounded transition-colors">MANAGE</button>
                </div>
              ))}
            </div>
          </div>
          <div className="bg-black border border-white/10 rounded-xl p-6 flex flex-col justify-center items-center">
             <h3 className="font-mono font-bold text-sm text-slate-400 mb-4 border-b border-white/5 pb-2 w-full">AUTOMATED ROOM SETUP</h3>
             <button onClick={() => send({ action: 'create_room' })} className="w-full max-w-sm bg-[#ff3366]/20 border border-[#ff3366]/50 text-[#ff3366] font-mono font-bold py-4 rounded-lg hover:bg-[#ff3366]/40 transition-colors shadow-[0_0_15px_rgba(255,51,102,0.2)]">
               CREATE CUSTOM ROOM (ID: AUTO)
             </button>
             <p className="text-[10px] font-mono text-slate-500 mt-4 text-center">AI will automatically create the room and invite the 16 registered teams.</p>
          </div>
        </div>"""
    },
    "teams": {
        "title": "TEAM MANAGEMENT CENTER",
        "subtitle": "Esports Roster & Performance Analytics",
        "content": """
        <div className="grid grid-cols-2 md:grid-cols-3 gap-6 mt-6 pb-12">
          {["SOUL", "GODL", "XSPARK", "REVN", "GLOBAL", "OR"].map(team => (
            <div key={team} className="bg-black border border-white/10 rounded-xl p-5 hover:border-[#00f0ff]/50 transition-colors cursor-pointer group" onClick={() => send({ action: 'view_team', team })}>
              <div className="flex justify-between items-center mb-4">
                <span className="font-mono font-bold text-white text-xl group-hover:text-[#00f0ff] transition-colors">{team}</span>
                <span className="bg-white/10 px-2 py-1 rounded text-[10px] font-mono text-slate-400 font-bold">ROSTER: 4/4</span>
              </div>
              <div className="space-y-2 text-xs font-mono text-slate-400 bg-white/5 p-3 rounded border border-white/5">
                <div className="flex justify-between"><span>Aggression Rating:</span><span className="text-[#00f0ff] font-bold">94</span></div>
                <div className="flex justify-between"><span>Clutch Rating:</span><span className="text-yellow-400 font-bold">82</span></div>
                <div className="flex justify-between"><span>Scrim Win %:</span><span className="text-white font-bold">65%</span></div>
              </div>
            </div>
          ))}
        </div>"""
    },
    "content": {
        "title": "AI CONTENT STUDIO",
        "subtitle": "Automated Highlights & Shorts Generation",
        "content": """
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mt-6">
          <div className="bg-black border border-white/10 rounded-xl p-6">
             <h3 className="font-mono font-bold text-sm text-slate-400 mb-4 border-b border-white/5 pb-2">GENERATION QUEUE</h3>
             <div className="space-y-3">
               {[
                 { title: "Soul Wipe - YouTube Shorts Format", status: "RENDERING (72%)", color: "text-yellow-400" },
                 { title: "Mortal Clutch - TikTok Format", status: "PENDING", color: "text-slate-400" },
                 { title: "Match 4 - Cinematic Recap", status: "COMPLETED", color: "text-green-400" },
               ].map(job => (
                 <div key={job.title} className="flex justify-between items-center bg-white/5 p-3 rounded border border-white/10 hover:border-white/20 transition-colors cursor-pointer" onClick={() => send({ action: 'view_job', job: job.title })}>
                   <span className="font-mono text-white text-xs">{job.title}</span>
                   <span className={`text-[10px] font-bold ${job.color}`}>{job.status}</span>
                 </div>
               ))}
             </div>
          </div>
          <div className="bg-black border border-white/10 rounded-xl p-6 flex flex-col items-center justify-center text-center">
            <button onClick={() => send({ action: 'generate_recap' })} className="w-full max-w-sm bg-[#00f0ff]/20 border border-[#00f0ff]/50 text-[#00f0ff] font-mono font-bold py-4 rounded-lg hover:bg-[#00f0ff]/40 transition-colors shadow-[0_0_15px_rgba(0,240,255,0.2)]">
              GENERATE MATCH RECAP VIDEO
            </button>
            <p className="text-xs text-slate-500 font-mono mt-4">AI will automatically detect squad wipes and clutches, stitch them, and generate a viral thumbnail.</p>
          </div>
        </div>"""
    },
    "social": {
        "title": "SOCIAL MEDIA DISTRIBUTION",
        "subtitle": "Auto-Publish & Thumbnail Generation",
        "content": """
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-6 mt-6">
          {['YouTube Shorts', 'Instagram Reels', 'TikTok', 'Discord Webhook'].map(plat => (
            <div key={plat} className="bg-black border border-white/10 rounded-xl p-6 hover:border-white/20 transition-colors">
              <div className="font-mono font-bold text-white mb-4 text-sm">{plat}</div>
              <div className="text-xs text-slate-400 font-mono space-y-2 mb-6 bg-white/5 p-3 rounded">
                <div className="flex justify-between"><span>Status:</span><span className="text-green-400 font-bold">Connected</span></div>
                <div className="flex justify-between"><span>Auto-post:</span><span className="text-yellow-400 font-bold">Enabled</span></div>
              </div>
              <button onClick={() => send({ action: 'test_post', plat })} className="w-full text-[10px] font-mono font-bold bg-white/5 text-slate-300 border border-white/10 py-2 rounded hover:bg-white/10 hover:text-white transition-colors">
                TEST CONNECTION
              </button>
            </div>
          ))}
        </div>"""
    },
    "training": {
        "title": "AI TRAINING CENTER",
        "subtitle": "Continuous Learning & Reinforcement Models",
        "content": """
        <div className="bg-black border border-white/10 rounded-xl p-6 mt-6">
          <h3 className="font-mono font-bold text-sm text-slate-400 mb-4 border-b border-white/5 pb-2">ACTIVE MODELS</h3>
          <div className="space-y-4">
            {[
              { name: "Director AI v2.4 (Reinforcement Learning)", loss: "0.012", status: "Training (Epoch 42/100)" },
              { name: "Fight Detection (YOLOv8 + Tracker)", loss: "0.004", status: "Deployed" },
              { name: "LLM Commentary Generator (Hindi Base)", loss: "1.24", status: "Fine-tuning" },
            ].map(m => (
              <div key={m.name} className="flex justify-between items-center bg-white/5 p-4 rounded border border-white/10">
                <div>
                  <div className="font-mono text-white text-sm font-bold">{m.name}</div>
                  <div className="font-mono text-xs text-slate-500 mt-1">Validation Loss: {m.loss}</div>
                </div>
                <div className="flex items-center gap-4">
                  <span className="text-xs font-mono text-yellow-400 font-bold">{m.status}</span>
                  <button onClick={() => send({ action: 'stop_training', model: m.name })} className="text-[10px] bg-red-500/20 hover:bg-red-500/30 text-red-400 border border-red-500/50 px-3 py-1.5 rounded font-bold transition-colors">STOP</button>
                </div>
              </div>
            ))}
          </div>
        </div>"""
    },
    "scrims": {
        "title": "SCRIM MANAGEMENT SYSTEM",
        "subtitle": "AI-Powered Practice Matches",
        "content": """
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mt-6">
          <div className="bg-black border border-white/10 rounded-xl p-6 flex flex-col items-center justify-center h-48 text-center">
             <button onClick={() => send({ action: 'start_scrim' })} className="w-full max-w-sm bg-yellow-500/20 border border-yellow-500/50 text-yellow-500 font-mono font-bold py-4 rounded-lg hover:bg-yellow-500/40 transition-colors shadow-[0_0_15px_rgba(234,179,8,0.2)]">
               INITIALIZE SCRIM LOBBY
             </button>
             <p className="text-[10px] text-slate-500 font-mono mt-4">Generates passwords, invites Discord teams, and starts scraping results.</p>
          </div>
          <div className="bg-black border border-white/10 rounded-xl p-6">
             <h3 className="font-mono font-bold text-sm text-slate-400 mb-4 border-b border-white/5 pb-2">AUTO POINTS TABLE</h3>
             <div className="font-mono text-xs text-slate-500 text-center py-8">
               No active scrims detected.
             </div>
          </div>
        </div>"""
    },
    "cloud": {
        "title": "CLOUD CONTROL CENTER",
        "subtitle": "Distributed GPU & Agent Orchestration",
        "content": """
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mt-6">
          {[
            { node: "Node-GPU-1", type: "RTX 4090", load: "92%", task: "Vision CV Pipeline" },
            { node: "Node-GPU-2", type: "RTX 3090", load: "45%", task: "Highlight Rendering" },
            { node: "Node-CPU-1", type: "EPYC 7742", load: "12%", task: "WebSocket Router" },
          ].map(n => (
            <div key={n.node} className="bg-black border border-white/10 rounded-xl p-5 hover:border-[#00f0ff]/50 transition-colors">
              <div className="flex justify-between items-center mb-4">
                <span className="font-mono font-bold text-white text-lg">{n.node}</span>
                <span className="text-[10px] bg-[#00f0ff]/20 text-[#00f0ff] px-2 py-0.5 rounded font-bold">{n.type}</span>
              </div>
              <div className="space-y-2 text-xs font-mono text-slate-400 bg-white/5 p-3 rounded border border-white/5">
                <div className="flex justify-between"><span>Load:</span><span className="text-yellow-400 font-bold">{n.load}</span></div>
                <div className="flex justify-between"><span>Task:</span><span className="text-white">{n.task}</span></div>
              </div>
              <button onClick={() => send({ action: 'restart_node', node: n.node })} className="mt-4 w-full text-[10px] font-mono font-bold text-slate-400 bg-white/5 border border-white/10 py-2 rounded hover:bg-white/10 hover:text-white transition-colors">
                RESTART NODE
              </button>
            </div>
          ))}
        </div>"""
    },
    "admin": {
        "title": "ADMIN & BUSINESS PANEL",
        "subtitle": "Broadcasters, Sponsors, & API Keys",
        "content": """
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mt-6">
          <div className="bg-black border border-white/10 rounded-xl p-6">
             <h3 className="font-mono font-bold text-sm text-slate-400 mb-4 border-b border-white/5 pb-2">SPONSOR MANAGEMENT</h3>
             <button onClick={() => send({ action: 'upload_sponsor' })} className="w-full text-xs font-mono font-bold text-slate-300 bg-white/5 border border-white/10 py-3 rounded hover:bg-white/10 hover:text-white transition-colors mb-4">
               UPLOAD SPONSOR ASSET (PNG/WEBM)
             </button>
             <div className="text-xs text-slate-500 font-mono bg-white/5 p-3 rounded">Current Active Sponsor: <span className="text-white font-bold">RedBull_Logo.png</span></div>
          </div>
          <div className="bg-black border border-white/10 rounded-xl p-6">
             <h3 className="font-mono font-bold text-sm text-slate-400 mb-4 border-b border-white/5 pb-2">API KEYS</h3>
             <div className="flex items-center gap-2 mb-3">
               <input type="password" value="sk_test_123456789_aegis_ai_prod" readOnly className="w-full bg-white/5 border border-white/10 rounded p-2 text-xs font-mono text-slate-400 outline-none" />
               <button onClick={() => send({ action: 'revoke_key' })} className="text-[10px] font-bold bg-red-500/20 hover:bg-red-500/30 text-red-400 border border-red-500/50 px-3 py-2.5 rounded transition-colors">REVOKE</button>
             </div>
             <div className="text-[10px] text-slate-500 font-mono">Production API Key for external tournament integration & OBS WebSockets.</div>
          </div>
        </div>"""
    }
}

template = '''"use client";
import { useAegisWS } from "@/hooks/use-aegis-ws";

export default function Page() {
  const { send } = useAegisWS();

  return (
    <div className="flex-1 p-6 overflow-y-auto bg-[#050508] w-full h-full">
      <div className="flex justify-between items-end mb-2">
        <div>
          <h2 className="text-2xl font-mono font-bold text-white mb-1">{title}</h2>
          <p className="text-sm text-slate-400 font-mono">{subtitle}</p>
        </div>
      </div>
      {content}
    </div>
  );
}
'''

base_dir = "/Users/nishantsingh/Downloads/aegis-ai-complete/frontend/src/app/(dashboard)"

for folder, data in pages.items():
    folder_path = os.path.join(base_dir, folder)
    os.makedirs(folder_path, exist_ok=True)
    file_path = os.path.join(folder_path, "page.tsx")
    
    file_content = template.replace("{title}", data["title"])
    file_content = file_content.replace("{subtitle}", data["subtitle"])
    file_content = file_content.replace("{content}", data["content"])
    
    with open(file_path, "w") as f:
        f.write(file_content)

print("Generated all missing pages successfully!")
