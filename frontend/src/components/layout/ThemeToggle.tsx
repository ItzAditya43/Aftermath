"use client";

import React, { useSyncExternalStore } from "react";
import { Moon, Sun } from "lucide-react";

const THEME_EVENT = "aftermath-theme-change";

function currentlyDark(): boolean {
  const explicit = document.documentElement.getAttribute("data-theme");
  if (explicit) return explicit === "dark";
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

function subscribe(onChange: () => void): () => void {
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  media.addEventListener("change", onChange);
  window.addEventListener(THEME_EVENT, onChange);
  return () => {
    media.removeEventListener("change", onChange);
    window.removeEventListener(THEME_EVENT, onChange);
  };
}

export default function ThemeToggle({ className = "" }: { className?: string }) {
  const dark = useSyncExternalStore(subscribe, currentlyDark, () => false);

  const toggle = () => {
    const next = currentlyDark() ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try {
      localStorage.setItem("aftermath-theme", next);
    } catch {
      // Storage can be blocked; the choice still applies for this visit.
    }
    window.dispatchEvent(new Event(THEME_EVENT));
  };

  return (
    <button
      type="button"
      onClick={toggle}
      className={`btn btn-quiet btn-sm btn-icon border-line ${className}`}
      aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
      title={dark ? "Light theme" : "Dark theme"}
    >
      {dark ? <Sun className="h-4 w-4" aria-hidden="true" /> : <Moon className="h-4 w-4" aria-hidden="true" />}
    </button>
  );
}
