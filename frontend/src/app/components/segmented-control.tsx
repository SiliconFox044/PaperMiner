import { motion } from "motion/react";

interface SegmentedControlProps {
  value: "library" | "opinion" | "qa";
  onChange: (value: "library" | "opinion" | "qa") => void;
}

export function SegmentedControl({ value, onChange }: SegmentedControlProps) {
  const tabs = [
    { id: "library" as const, label: "文献库" },
    { id: "opinion" as const, label: "观点搜索" },
    { id: "qa" as const, label: "知识问答" },
  ];

  return (
    <div className="flex justify-center py-4 border-b border-black/[0.04]">
      <div className="relative inline-flex bg-white rounded-full p-1 border border-black/[0.04] shadow-sm">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => onChange(tab.id)}
            className={`relative px-8 py-2.5 rounded-full transition-colors duration-200 text-sm tracking-wide ${
              value === tab.id ? "text-foreground font-medium" : "text-muted-foreground hover:text-foreground"
            }`}
          >
            {value === tab.id && (
              <motion.div
                layoutId="activeSegment"
                className="absolute inset-0 bg-[#f3f4f6] rounded-full"
                transition={{ type: "spring", bounce: 0.15, duration: 0.4 }}
              />
            )}
            <span className="relative z-10 font-bold text-[#d97757]">{tab.label}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
