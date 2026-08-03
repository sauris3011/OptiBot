const PIPELINE = [
  { step: "01", name: "Input guardrails", detail: "Injection patterns, length cap, rate limit — optimized pipeline only." },
  { step: "02", name: "Semantic cache", detail: "Cosine-similarity lookup over prior queries, partitioned by resolved facts." },
  { step: "03", name: "Classifier", detail: "Keyword/pattern heuristics sort each query into Tier 1 / 2 / 3 — no LLM call." },
  { step: "04", name: "Prompt builder", detail: "Baseline verbose template vs. optimized structured role/context/task template." },
  { step: "05", name: "Retrieve", detail: "RAG over embedded policy docs (cosine similarity) or direct order-record lookup." },
  { step: "06", name: "LiteLLM gateway", detail: "Routes to the baseline / simple / complex model alias for this request." },
  { step: "07", name: "Validate & log", detail: "PII masking, output checks against order data, audit + interaction logging." },
];

const STACK = [
  { name: "Frontend", tech: "Next.js 15", purpose: "Chat interface, mode toggle, dashboard, before/after and governance views." },
  { name: "API gateway", tech: "FastAPI", purpose: "Request handling, middleware chain, orchestration of the pipeline." },
  { name: "LLM router", tech: "LiteLLM", purpose: "Gateway client for model calls, token/cost accounting, per-slot alias routing." },
  { name: "Vector store", tech: "numpy", purpose: "In-process cosine-similarity matrix over the embedded policy corpus." },
  { name: "Embeddings", tech: "sentence-transformers", purpose: "all-MiniLM-L6-v2, with a lexical fallback — shared by RAG and the cache." },
  { name: "Semantic cache", tech: "In-memory dict", purpose: "Caches semantically similar queries, optimized pipeline only." },
  { name: "Order data", tech: "Static JSON", purpose: "Synthetic orders, customers, products and shipments, read-only." },
  { name: "Guardrails", tech: "Custom service", purpose: "Injection detection, rate limiting, output verification against order data." },
  { name: "Metrics store", tech: "SQLite", purpose: "Interaction metrics, audit records, before/after comparison data." },
];

const TIERS = [
  { label: "Tier 1 · Simple", route: "Direct order lookup → gateway simple slot", cost: "cheapest configured model" },
  { label: "Tier 2 · Medium", route: "Policy & FAQ questions → gateway complex slot, RAG-grounded", cost: "capable model + retrieval" },
  { label: "Tier 3 · Complex", route: "Multi-order disputes → complex slot, enriched context", cost: "capable model, more context" },
];

export default function ArchitecturePage() {
  return (
    <>
      <div className="eyebrow">Solution architecture</div>
      <h1 className="page-title">One request, six optimization layers</h1>
      <p className="page-sub">
        Request pipeline · FastAPI middleware chain · LiteLLM routing. Model
        identity is not hardcoded — OptiBot picks a per-tier gateway alias
        (baseline / simple / complex) configured via backend/.env or the
        gear-icon settings panel, so any gateway-exposed model works without a
        code change.
      </p>

      <div className="card" style={{ marginBottom: 16 }}>
        <h2 className="card-title">Request pipeline</h2>
        <div className="arch-pipeline">
          {PIPELINE.map((p, i) => (
            <div className="arch-step-wrap" key={p.step}>
              <div className="arch-step">
                <span className="arch-step-num">{p.step}</span>
                <span className="arch-step-name">{p.name}</span>
                <span className="arch-step-detail">{p.detail}</span>
              </div>
              {i < PIPELINE.length - 1 && (
                <div className="arch-arrow">
                  <div className="arch-arrow-line" />
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <h2 className="card-title">Component stack</h2>
        <div className="grid grid-3">
          {STACK.map((s) => (
            <div className="stack-card" key={s.name}>
              <div className="stack-card-head">
                <span className="stack-card-name">{s.name}</span>
                <span className="stack-card-tech">{s.tech}</span>
              </div>
              <span className="stack-card-purpose">{s.purpose}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="card">
        <h2 className="card-title">Query complexity tiers</h2>
        <div className="grid grid-3">
          {TIERS.map((t) => (
            <div className="tier-card" key={t.label}>
              <span className="tier-card-label">{t.label}</span>
              <span className="tier-card-route">{t.route}</span>
              <span className="tier-card-cost">{t.cost}</span>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}
