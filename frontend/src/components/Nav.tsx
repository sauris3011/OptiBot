"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { getHealth } from "@/lib/api";
import type { HealthResponse } from "@/lib/types";

const LINKS = [
  { href: "/", label: "Chat" },
  { href: "/dashboard", label: "Dashboard" },
  { href: "/comparison", label: "Before / After" },
  { href: "/governance", label: "Governance" },
];

export default function Nav() {
  const pathname = usePathname();
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [down, setDown] = useState(false);

  useEffect(() => {
    getHealth()
      .then(setHealth)
      .catch(() => setDown(true));
  }, []);

  return (
    <nav className="nav">
      <div className="brand">
        Opti<span>Bot</span>
      </div>
      {LINKS.map((l) => (
        <Link
          key={l.href}
          href={l.href}
          className={`nav-link ${pathname === l.href ? "active" : ""}`}
        >
          {l.label}
        </Link>
      ))}
      <div className="nav-right">
        {down && <span className="pill bad">API offline</span>}
        {health && (
          <>
            {!health.api_key_configured && (
              <span className="pill warn">No API key</span>
            )}
            <span
              className="pill"
              title={`Embedding backend: ${health.embedding_backend}`}
            >
              {health.embedding_backend.startsWith("sentence-transformers")
                ? "semantic embeddings"
                : "lexical fallback"}
            </span>
            <span className="pill info">{health.rag_index.chunks} policy chunks</span>
          </>
        )}
      </div>
    </nav>
  );
}
