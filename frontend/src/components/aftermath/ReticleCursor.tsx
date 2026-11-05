"use client";

import { useEffect, useRef } from "react";

type Props = { nodeHovered: boolean };

export default function ReticleCursor({ nodeHovered }: Props) {
  const cursorRef = useRef<HTMLDivElement>(null);
  const nodeHoveredRef = useRef(nodeHovered);

  useEffect(() => {
    nodeHoveredRef.current = nodeHovered;
    if (cursorRef.current) {
      cursorRef.current.dataset.nodeHovered = nodeHovered ? "true" : "false";
      cursorRef.current.dataset.interactive = nodeHovered ? "true" : cursorRef.current.dataset.interactive ?? "false";
    }
  }, [nodeHovered]);

  useEffect(() => {
    const cursor = cursorRef.current;
    if (!cursor) return;
    const finePointer = window.matchMedia("(hover: hover) and (pointer: fine)");
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    let x = -100;
    let y = -100;
    let targetX = x;
    let targetY = y;
    let frame = 0;
    let visible = false;
    let enabled = false;

    const animate = () => {
      frame = 0;
      x += (targetX - x) * 0.28;
      y += (targetY - y) * 0.28;
      cursor.style.transform = `translate3d(${x}px, ${y}px, 0) translate(-50%, -50%)`;
      if (Math.abs(targetX - x) + Math.abs(targetY - y) > 0.2) frame = window.requestAnimationFrame(animate);
    };
    const move = (event: PointerEvent) => {
      if (!enabled) return;
      targetX = event.clientX;
      targetY = event.clientY;
      const target = event.target instanceof Element ? event.target : null;
      const textInput = Boolean(target?.closest("input:not([type=button]):not([type=submit]), textarea, [contenteditable=true]"));
      const interactive = Boolean(target?.closest("button, a, select, [role=button], canvas"));
      cursor.dataset.hidden = textInput || !visible ? "true" : "false";
      cursor.dataset.interactive = interactive || nodeHoveredRef.current ? "true" : "false";
      cursor.dataset.nodeHovered = nodeHoveredRef.current ? "true" : "false";
      if (!frame) frame = window.requestAnimationFrame(animate);
    };
    const enter = () => {
      if (!enabled) return;
      visible = true;
      cursor.dataset.hidden = "false";
    };
    const leave = (event?: PointerEvent) => {
      if (event?.relatedTarget) return;
      visible = false;
      cursor.dataset.hidden = "true";
    };
    const updateEnabled = () => {
      enabled = finePointer.matches && !reducedMotion.matches;
      if (enabled) {
        document.body.classList.add("has-reticle-cursor");
      } else {
        document.body.classList.remove("has-reticle-cursor");
        visible = false;
        cursor.dataset.hidden = "true";
        if (frame) window.cancelAnimationFrame(frame);
        frame = 0;
      }
    };
    const motionChange = () => {
      updateEnabled();
    };
    window.addEventListener("pointermove", move, { passive: true });
    window.addEventListener("pointerover", enter, { passive: true });
    window.addEventListener("pointerout", leave, { passive: true });
    reducedMotion.addEventListener("change", motionChange);
    finePointer.addEventListener("change", motionChange);
    updateEnabled();
    return () => {
      if (frame) window.cancelAnimationFrame(frame);
      document.body.classList.remove("has-reticle-cursor");
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerover", enter);
      window.removeEventListener("pointerout", leave);
      reducedMotion.removeEventListener("change", motionChange);
      finePointer.removeEventListener("change", motionChange);
    };
  }, []);

  return <div ref={cursorRef} aria-hidden="true" className="reticle-cursor"><span /></div>;
}
