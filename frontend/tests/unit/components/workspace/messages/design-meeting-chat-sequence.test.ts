import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "@rstest/core";

const MESSAGE_LIST = readFileSync(
  join(process.cwd(), "src/components/workspace/messages/message-list.tsx"),
  "utf8",
);
const MESSAGE_LIST_ITEM = readFileSync(
  join(
    process.cwd(),
    "src/components/workspace/messages/message-list-item.tsx",
  ),
  "utf8",
);

describe("Design meeting chat sequence", () => {
  it("anchors the durable Meeting card to its run without restoring the checkpoint card", () => {
    expect(MESSAGE_LIST).toContain("DebatePanel");
    expect(MESSAGE_LIST).toContain("meetingAnchorGroupIndices");
    expect(MESSAGE_LIST).toContain('group.type === "assistant:present-files"');
    expect(MESSAGE_LIST).toContain('group.type === "assistant"');
    expect(MESSAGE_LIST).toContain("UnanchoredMeetings");
    expect(MESSAGE_LIST_ITEM).not.toContain("DesignMeetingProgressCard");
    expect(MESSAGE_LIST_ITEM).not.toContain("readDesignMeetingProgress");
  });
});
