import os
import sys

tree = """
├── frontend/
│   ├── package.json
│   ├── components.json
│   ├── public/
│   │   ├── assets/
│   │   │   ├── logos/
│   │   │   │   ├── aegis-logo.svg
│   │   │   │   └── aegis-logo-white.svg
│   │   │   ├── maps/
│   │   │   │   ├── erangel.jpg
│   │   │   │   ├── miramar.jpg
│   │   │   │   └── sanhok.jpg
│   │   │   └── images/
│   │   │       ├── hero-bg.jpg
│   │   │       └── placeholder-team.png
│   │   ├── fonts/
│   │   │   ├── Inter-Variable.woff2
│   │   │   └── JetBrainsMono-Variable.woff2
│   │   └── favicon.ico
│   └── src/
│       ├── app/
│       │   ├── layout.tsx
│       │   ├── page.tsx
│       │   ├── loading.tsx
│       │   ├── error.tsx
│       │   ├── not-found.tsx
│       │   ├── (auth)/
│       │   │   ├── layout.tsx
│       │   │   ├── login/
│       │   │   │   └── page.tsx
│       │   │   ├── register/
│       │   │   │   └── page.tsx
│       │   │   └── forgot-password/
│       │   │       └── page.tsx
│       │   ├── (dashboard)/
│       │   │   ├── layout.tsx
│       │   │   ├── page.tsx
│       │   │   ├── tournaments/
│       │   │   │   ├── page.tsx
│       │   │   │   ├── create/
│       │   │   │   │   └── page.tsx
│       │   │   │   └── [tournamentId]/
│       │   │   │       ├── page.tsx
│       │   │   │       ├── layout.tsx
│       │   │   │       ├── matches/
│       │   │   │       │   └── page.tsx
│       │   │   │       ├── teams/
│       │   │   │       │   └── page.tsx
│       │   │   │       ├── leaderboard/
│       │   │   │       │   └── page.tsx
│       │   │   │       └── settings/
│       │   │   │           └── page.tsx
│       │   │   ├── matches/
│       │   │   │   └── [matchId]/
│       │   │   │       ├── page.tsx
│       │   │   │       ├── live/
│       │   │   │       │   └── page.tsx
│       │   │   │       ├── analytics/
│       │   │   │       │   └── page.tsx
│       │   │   │       ├── highlights/
│       │   │   │       │   └── page.tsx
│       │   │   │       └── replay/
│       │   │   │           └── page.tsx
│       │   │   ├── teams/
│       │   │   │   ├── page.tsx
│       │   │   │   └── [teamId]/
│       │   │   │       ├── page.tsx
│       │   │   │       ├── players/
│       │   │   │       │   └── page.tsx
│       │   │   │       └── analytics/
│       │   │   │           └── page.tsx
│       │   │   ├── players/
│       │   │   │   ├── page.tsx
│       │   │   │   └── [playerId]/
│       │   │   │       ├── page.tsx
│       │   │   │       └── highlights/
│       │   │   │           └── page.tsx
│       │   │   ├── analytics/
│       │   │   │   ├── page.tsx
│       │   │   │   ├── comparative/
│       │   │   │   │   └── page.tsx
│       │   │   │   └── trends/
│       │   │   │       └── page.tsx
│       │   │   ├── highlights/
│       │   │   │   ├── page.tsx
│       │   │   │   └── [highlightId]/
│       │   │   │       └── page.tsx
│       │   │   ├── broadcast/
│       │   │   │   ├── page.tsx
│       │   │   │   ├── director/
│       │   │   │   │   └── page.tsx
│       │   │   │   └── overlays/
│       │   │   │       └── page.tsx
│       │   │   └── admin/
│       │   │       ├── page.tsx
│       │   │       ├── users/
│       │   │       │   └── page.tsx
│       │   │       ├── system/
│       │   │       │   └── page.tsx
│       │   │       └── logs/
│       │   │           └── page.tsx
│       │   └── api/
│       │       ├── auth/
│       │       │   └── [...nextauth]/
│       │       │       └── route.ts
│       │       └── webhook/
│       │           └── route.ts
│       ├── components/
│       │   ├── ui/
│       │   │   ├── accordion.tsx
│       │   │   ├── alert.tsx
│       │   │   ├── avatar.tsx
│       │   │   ├── badge.tsx
│       │   │   ├── button.tsx
│       │   │   ├── card.tsx
│       │   │   ├── input.tsx
│       │   │   └── toast.tsx
│       │   ├── layout/
│       │   │   ├── sidebar.tsx
│       │   │   ├── header.tsx
│       │   │   └── breadcrumbs.tsx
│       │   ├── live/
│       │   │   ├── match-viewer.tsx
│       │   │   ├── kill-feed.tsx
│       │   │   └── commentary-display.tsx
│       │   ├── analytics/
│       │   │   ├── points-table.tsx
│       │   │   └── heatmap.tsx
│       │   ├── broadcast/
│       │   │   ├── director-panel.tsx
│       │   │   └── replay-controls.tsx
│       │   ├── highlights/
│       │   │   └── clip-card.tsx
│       │   └── shared/
│       │       ├── team-logo.tsx
│       │       └── player-avatar.tsx
│       ├── hooks/
│       │   ├── use-live-events.ts
│       │   ├── use-match-state.ts
│       │   ├── use-websocket.ts
│       │   └── use-broadcast-control.ts
│       ├── lib/
│       │   ├── api-client.ts
│       │   ├── ws-client.ts
│       │   ├── utils.ts
│       │   └── constants.ts
│       ├── stores/
│       │   ├── auth-store.ts
│       │   ├── match-store.ts
│       │   └── ui-store.ts
│       ├── types/
│       │   ├── api.ts
│       │   ├── events.ts
│       │   └── match.ts
│       └── styles/
│           ├── globals.css
│           └── themes.css
├── overlays/
│   ├── package.json
│   └── src/
│       ├── app/
│       │   ├── layout.tsx
│       │   └── overlays/
│       │       ├── live/
│       │       │   ├── page.tsx
│       │       │   └── components/
│       │       │       ├── scoreboard.tsx
│       │       │       ├── kill-feed.tsx
│       │       │       └── minimap-overlay.tsx
│       │       ├── replay/
│       │       │   ├── page.tsx
│       │       │   └── components/
│       │       │       └── replay-info.tsx
│       │       ├── post-match/
│       │       │   ├── page.tsx
│       │       │   └── components/
│       │       │       └── match-result.tsx
│       │       ├── starting/
│       │       │   ├── page.tsx
│       │       │   └── components/
│       │       │       └── match-intro.tsx
│       │       └── intermission/
│       │           ├── page.tsx
│       │           └── components/
│       │               └── break-timer.tsx
│       ├── components/
│       │   ├── animations/
│       │   │   └── fade-in.tsx
│       │   └── shared/
│       │       └── team-logo.tsx
│       ├── hooks/
│       │   └── use-overlay-events.ts
│       ├── lib/
│       │   └── overlay-client.ts
│       └── types/
│           └── overlay-events.ts
└── shared/
    ├── types/
    │   ├── package.json
    │   └── src/
    │       └── index.ts
    ├── constants/
    │   ├── package.json
    │   └── src/
    │       └── index.ts
    └── utils/
        ├── package.json
        └── src/
            └── index.ts
"""

import re

def process_tree(base_path, tree_str):
    lines = tree_str.strip().split('\\n')
    path_stack = []
    
    for line in lines:
        if not line.strip():
            continue
            
        # Count leading tree characters to determine depth
        match = re.match(r'^([│\s├──└──]*)(.+?)(?:\s+#.*)?$', line)
        if not match:
            continue
            
        prefix = match.group(1)
        name = match.group(2).strip()
        
        # Calculate depth based on prefix length (each level is roughly 4 chars)
        depth = len(prefix) // 4
        
        # Truncate stack to current depth
        path_stack = path_stack[:depth]
        path_stack.append(name)
        
        full_path = os.path.join(base_path, *[p.replace('/', '') for p in path_stack])
        
        if name.endswith('/'):
            os.makedirs(full_path, exist_ok=True)
        else:
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            if not os.path.exists(full_path):
                with open(full_path, 'w') as f:
                    if name.endswith('.tsx'):
                        f.write(f'export default function Component() {{ return <div>{name}</div>; }}\n')
                    elif name.endswith('.ts'):
                        f.write('export {};\n')
                    else:
                        f.write('')

if __name__ == "__main__":
    process_tree(sys.argv[1], tree)
