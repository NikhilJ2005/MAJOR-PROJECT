"use client";

import { useState } from "react";
import { FEATURES } from "@/lib/api";

type Role = { name: string; signup: boolean };
type Draft = { what: string; roles: Role[]; things: string[]; rules: string; features: string[] };

const EMPTY: Draft = { what: "", roles: [], things: [], rules: "", features: [] };

// Starters: each is a complete, testable description the agents build well.
const TEMPLATES: { title: string; blurb: string; draft: Draft }[] = [
  {
    title: "Freelance marketplace with escrow",
    blurb: "Clients post projects, freelancers bid, money is held until work is approved.",
    draft: {
      what: "A freelance marketplace where clients post projects, freelancers bid, and payments are held in escrow per milestone.",
      roles: [{ name: "client", signup: true }, { name: "freelancer", signup: true }, { name: "dispute admin", signup: false }],
      things: ["Project (title, budget, status)", "Bid (amount, message)", "Milestone (title, amount, state)", "Wallet (balance)", "Ledger entry (amount, debit or credit)"],
      rules: "Only clients create projects and milestones; only freelancers bid.\nA milestone goes draft → funded → submitted → approved, or disputed.\nFunding moves the amount from the client's wallet into escrow; it fails with 409 if the balance is too low.\nApproving releases escrow to the freelancer; every money movement writes two ledger entries.\nOnly a dispute admin resolves disputes.",
      features: ["refresh_tokens", "account_lockout", "audit_log", "rate_limit"],
    },
  },
  {
    title: "Clinic appointments",
    blurb: "Patients book doctors' free slots; no double booking.",
    draft: {
      what: "A clinic booking app where patients book appointments with doctors.",
      roles: [{ name: "patient", signup: true }, { name: "doctor", signup: false }],
      things: ["Doctor (name, specialty)", "Slot (start time, doctor)", "Appointment (slot, patient, status)"],
      rules: "A slot can be booked once; booking a taken slot fails with 409.\nPatients cancel their own appointments up to 24 hours before.\nDoctors mark appointments as done or no-show.",
      features: ["audit_log", "rate_limit"],
    },
  },
  {
    title: "Helpdesk",
    blurb: "Customers open tickets, agents answer, SLAs tracked.",
    draft: {
      what: "A helpdesk where customers open support tickets and agents resolve them.",
      roles: [{ name: "customer", signup: true }, { name: "agent", signup: false }],
      things: ["Ticket (subject, description, priority, status)", "Reply (message)", "Tag (name)"],
      rules: "Tickets go open → in progress → resolved → closed; a resolved ticket can be reopened.\nCustomers see only their own tickets.\nOnly agents assign tickets and change priority.",
      features: ["audit_log"],
    },
  },
  {
    title: "Inventory and orders",
    blurb: "Stock per warehouse, orders that reserve stock.",
    draft: {
      what: "An inventory system for a small shop with warehouses, products and customer orders.",
      roles: [{ name: "customer", signup: true }, { name: "staff", signup: false }],
      things: ["Warehouse (name)", "Product (name, price, stock)", "Order (status)", "Order item (product, quantity)"],
      rules: "Placing an order reserves stock; it fails with 409 if stock is too low.\nCancelling an order puts the stock back.\nOnly staff change prices and stock.",
      features: ["rate_limit"],
    },
  },
  {
    title: "CRM",
    blurb: "Companies, contacts and a deal pipeline.",
    draft: {
      what: "A CRM for a sales team: companies, contacts and deals moving through a pipeline.",
      roles: [{ name: "sales rep", signup: true }, { name: "manager", signup: false }],
      things: ["Company (name, industry)", "Contact (name, email)", "Deal (title, value, stage)", "Activity (note, due date)"],
      rules: "Deals move lead → qualified → proposal → won or lost.\nReps see their own deals; managers see all.\nA won deal cannot change value.",
      features: ["audit_log", "refresh_tokens"],
    },
  },
  {
    title: "Course platform",
    blurb: "Instructors publish courses, students enrol and track progress.",
    draft: {
      what: "An online course platform where instructors publish courses and students enrol and track lessons.",
      roles: [{ name: "student", signup: true }, { name: "instructor", signup: true }],
      things: ["Course (title, price, published)", "Lesson (title, position)", "Enrollment (progress)", "Review (rating 1-5, comment)"],
      rules: "Only instructors create courses and lessons.\nStudents review a course once, only after enrolling.\nProgress is between 0 and 100.",
      features: ["rate_limit"],
    },
  },
];

function toPrompt(d: Draft): string {
  const parts = [d.what.trim()];
  if (d.roles.length) {
    const roles = d.roles.map((r) => `${r.name}${r.signup ? "" : " (given by an admin)"}`).join(", ");
    parts.push(`Users: ${roles}.`);
  }
  if (d.things.length) parts.push(`It stores: ${d.things.join("; ")}.`);
  const rules = d.rules.split("\n").map((r) => r.trim()).filter(Boolean);
  if (rules.length) parts.push(`Rules:\n${rules.map((r) => `- ${r}`).join("\n")}`);
  if (d.features.length) parts.push(`Production features: ${d.features.map((f) => FEATURES[f].toLowerCase()).join(", ")}.`);
  return parts.filter(Boolean).join("\n\n");
}

/** Templates and a guided form that write a strong prompt; the user can still edit the result. */
export function PromptBuilder({ onUse, onClose }: { onUse: (prompt: string) => void; onClose: () => void }) {
  const [d, setD] = useState<Draft>(EMPTY);
  const [role, setRole] = useState("");
  const [thing, setThing] = useState("");
  const prompt = toPrompt(d);
  const missing = [!d.roles.length && "who uses it", !d.things.length && "what it stores", !d.rules.trim() && "its rules"].filter(Boolean);
  const input = "w-full rounded-lg border border-ink-700 bg-ink-950 px-3 py-2 text-sm text-ink-100 outline-none focus:border-ink-500";

  return (
    <div className="fixed inset-0 z-40 grid place-items-center bg-black/60 p-4" role="dialog" aria-label="Guided prompt">
      <div className="flex max-h-[90vh] w-full max-w-4xl flex-col overflow-hidden rounded-2xl border border-ink-700 bg-ink-900 shadow-2xl">
        <div className="flex items-center border-b border-ink-800 px-5 py-3">
          <h2 className="font-display text-lg text-ink-50">Describe your app step by step</h2>
          <button onClick={onClose} className="ml-auto text-sm text-ink-400 hover:text-ink-50">Close</button>
        </div>
        <div className="grid min-h-0 flex-1 gap-5 overflow-y-auto p-5 lg:grid-cols-[1fr_320px]">
          <div className="space-y-5">
            <section>
              <p className="mb-2 text-sm text-ink-300">Start from an example</p>
              <div className="grid gap-2 sm:grid-cols-2">
                {TEMPLATES.map((t) => (
                  <button key={t.title} onClick={() => setD(t.draft)} className="rounded-lg border border-ink-700 p-3 text-left hover:border-signal-400">
                    <span className="block text-sm font-medium text-ink-100">{t.title}</span>
                    <span className="block text-xs text-ink-400">{t.blurb}</span>
                  </button>
                ))}
              </div>
            </section>
            <label className="block text-sm text-ink-300">What is it?
              <textarea rows={2} value={d.what} onChange={(e) => setD({ ...d, what: e.target.value })} className={`mt-1 ${input}`}
                placeholder="A booking app for a yoga studio…" />
            </label>
            <section>
              <p className="text-sm text-ink-300">Who uses it?</p>
              <div className="mt-1 flex flex-wrap gap-2">
                {d.roles.map((r, i) => (
                  <span key={r.name} className="flex items-center gap-1.5 rounded-full bg-ink-800 px-3 py-1 text-sm text-ink-100">
                    {r.name}
                    <button onClick={() => setD({ ...d, roles: d.roles.map((x, j) => (j === i ? { ...x, signup: !x.signup } : x)) })}
                      className="text-[11px] text-ink-400 hover:text-ink-100" title="Can people pick this role when they sign up?">
                      {r.signup ? "signs up" : "admin gives"}
                    </button>
                    <button onClick={() => setD({ ...d, roles: d.roles.filter((_, j) => j !== i) })} aria-label={`remove ${r.name}`} className="text-ink-500 hover:text-rose-300">×</button>
                  </span>
                ))}
                <form onSubmit={(e) => { e.preventDefault(); if (role.trim()) { setD({ ...d, roles: [...d.roles, { name: role.trim(), signup: true }] }); setRole(""); } }}>
                  <input value={role} onChange={(e) => setRole(e.target.value)} placeholder="+ a kind of user" className="w-40 rounded-full border border-ink-700 bg-ink-950 px-3 py-1 text-sm text-ink-100 outline-none" />
                </form>
              </div>
            </section>
            <section>
              <p className="text-sm text-ink-300">What does it store?</p>
              <div className="mt-1 flex flex-wrap gap-2">
                {d.things.map((t, i) => (
                  <span key={t} className="flex items-center gap-1.5 rounded-full bg-ink-800 px-3 py-1 text-sm text-ink-100">
                    {t}
                    <button onClick={() => setD({ ...d, things: d.things.filter((_, j) => j !== i) })} aria-label={`remove ${t}`} className="text-ink-500 hover:text-rose-300">×</button>
                  </span>
                ))}
                <form onSubmit={(e) => { e.preventDefault(); if (thing.trim()) { setD({ ...d, things: [...d.things, thing.trim()] }); setThing(""); } }}>
                  <input value={thing} onChange={(e) => setThing(e.target.value)} placeholder="+ e.g. Booking (date, seats)" className="w-56 rounded-full border border-ink-700 bg-ink-950 px-3 py-1 text-sm text-ink-100 outline-none" />
                </form>
              </div>
            </section>
            <label className="block text-sm text-ink-300">Rules, one per line
              <textarea rows={4} value={d.rules} onChange={(e) => setD({ ...d, rules: e.target.value })} className={`mt-1 ${input}`}
                placeholder={"A class has a capacity; booking a full class fails with 409.\nMembers cancel their own bookings."} />
            </label>
            <section>
              <p className="text-sm text-ink-300">Production features</p>
              <div className="mt-1 flex flex-wrap gap-2">
                {Object.entries(FEATURES).map(([key, label]) => {
                  const on = d.features.includes(key);
                  return (
                    <button key={key} aria-pressed={on} onClick={() => setD({ ...d, features: on ? d.features.filter((f) => f !== key) : [...d.features, key] })}
                      className={`rounded-full border px-3 py-1 text-sm ${on ? "border-signal-400 bg-signal-400/15 text-signal-200" : "border-ink-700 text-ink-400 hover:text-ink-100"}`}>
                      {on ? "✓ " : ""}{label}
                    </button>
                  );
                })}
              </div>
            </section>
          </div>
          <aside className="flex flex-col rounded-xl border border-ink-800 bg-ink-950 p-3">
            <p className="text-xs text-ink-400">Your prompt</p>
            <pre className="mt-1 flex-1 overflow-auto whitespace-pre-wrap text-sm leading-relaxed text-ink-100">{prompt || "Pick an example or fill in the form."}</pre>
            {prompt && missing.length > 0 && <p className="mt-2 text-xs text-signal-300">Stronger with: {missing.join(", ")}.</p>}
            <button disabled={prompt.length < 10} onClick={() => onUse(prompt)}
              className="mt-3 rounded-lg bg-signal-400 px-4 py-2 text-sm font-semibold text-ink-950 hover:bg-signal-300 disabled:opacity-40">
              Use this prompt
            </button>
          </aside>
        </div>
      </div>
    </div>
  );
}
