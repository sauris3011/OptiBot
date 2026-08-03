"use client";

import dynamic from "next/dynamic";

// The chat transcript is seeded from sessionStorage (see ChatClient) so it
// survives client-side navigation and a hard refresh — but that means its
// very first render can only ever be correct in the browser: the server has
// no sessionStorage to read, so an SSR pass here would render an empty
// transcript that then mismatches whatever the client actually restores,
// tripping a hydration error. Skipping SSR for this component avoids that
// class of bug entirely rather than papering over one instance of it.
const ChatClient = dynamic(() => import("./ChatClient"), {
  ssr: false,
  loading: () => <div className="empty">Loading chat…</div>,
});

export default function Page() {
  return <ChatClient />;
}
