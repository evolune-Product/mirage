"use client";
import { useRef } from "react";
import { motion, useScroll, useSpring, useReducedMotion } from "framer-motion";

const steps = [
  ["Create a replica", "Submit a training video URL. The person on camera speaks a verification phrase to give consent."],
  ["Define a persona", "Write a system prompt, attach knowledge, pick a replica and voice."],
  ["Start a conversation", "POST /v1/conversations with the persona id, then open the WebSocket stream."],
  ["Ship it", "Embed the widget or call the API from your own app. Meter usage per minute."],
];
export default function HowItWorks() {
  const ref = useRef<HTMLDivElement>(null);
  const reduce = useReducedMotion();
  const { scrollYProgress } = useScroll({ target: ref, offset: ["start 75%", "end 60%"] });
  const h = useSpring(scrollYProgress, { stiffness: 90, damping: 24 });
  return (
    <div className="mx-auto mt-24 max-w-3xl">
      <p className="eyebrow text-center">Step by step</p>
      <h3 className="mt-3 text-center font-display text-4xl text-white md:text-5xl">How it works</h3>
      <div ref={ref} className="relative mt-12 pl-12 md:pl-16">
        <div className="absolute bottom-2 left-[17px] top-2 w-px bg-white/15 md:left-[23px]" />
        <motion.div className="absolute left-[16px] top-2 w-[3px] origin-top rounded-full bg-vocalface-gradient md:left-[22px]" style={{ height: reduce ? "100%" : "calc(100% - 16px)", scaleY: reduce ? 1 : h }} />
        <ol className="space-y-12">
          {steps.map(([t, d], i) => (
            <li key={t} className="relative">
              <span className="absolute -left-12 top-0 grid h-9 w-9 place-items-center rounded-full border border-white/20 bg-ink font-mono text-sm text-white md:-left-16 md:h-12 md:w-12">{i + 1}</span>
              <h4 className="font-display text-3xl text-white">{t}</h4><p className="mt-2 text-gray-300">{d}</p>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}
