import { SkillsPageContent } from "@/components/workspace/skills/skills-page";

export default function SkillsPage() {
  return (
    <div className="size-full overflow-y-auto">
      <div className="mx-auto w-full max-w-5xl p-6 sm:p-8">
        <SkillsPageContent />
      </div>
    </div>
  );
}
