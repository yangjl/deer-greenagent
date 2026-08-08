/**
 * Meetings as durable entries in a conversation, one per run that held one.
 *
 * The debate panel used to fold every council seat in the thread into a single
 * block, splice it into the transcript at a computed index, and bind it to
 * `latestRunId`. That produced three failures at once: a second meeting merged
 * into the first's rounds, starting any new run moved the panel off the meeting
 * it described, and a meeting the browser never watched live left nothing to
 * render.
 *
 * The run is the identity that fixes all three. A meeting already has one —
 * `subagent.start` records it, so it survives a reload and a background run —
 * and the transcript is ordered by it, so a meeting can sit where it actually
 * happened instead of floating at the end.
 *
 * Pure and side-effect free: the rendering layer decides what to show, this
 * decides what a meeting *is*.
 */

import { debateRounds } from "./council-seat";
import type { Subtask } from "./types";

export interface Meeting {
  /** The run that held it. `""` for a seat that arrived without one. */
  runId: string;
  /** The DBTL stage it belongs to, when its seats agree on one. */
  stage?: string;
  rounds: { round: number; seats: Subtask[] }[];
  seats: Subtask[];
  /** Seats that have finished, however they finished. */
  reported: number;
  total: number;
  isRunning: boolean;
}

export interface MeetingTranscriptGroup {
  runId?: string;
  type: string;
  /** Stage named by a presented DBTL result, when this is one. */
  stage?: string;
  /** Position of the group's first message in the unfiltered thread. */
  firstMessageIndex?: number;
}

/** Pick one transcript group to own each run-scoped meeting card. */
export function meetingAnchorIndices(
  groups: readonly MeetingTranscriptGroup[],
): Set<number> {
  const anchors = new Map<string, { index: number; priority: number }>();
  groups.forEach((group, index) => {
    if (!group.runId || group.type === "human") {
      return;
    }
    const priority =
      group.type === "assistant:present-files"
        ? 3
        : group.type === "assistant"
          ? 2
          : 1;
    const existing = anchors.get(group.runId);
    if (!existing || priority >= existing.priority) {
      anchors.set(group.runId, { index, priority });
    }
  });
  return new Set([...anchors.values()].map(({ index }) => index));
}

/** Bind each meeting to the transcript group that should render it. */
export function meetingAnchorRunIds(
  groups: readonly MeetingTranscriptGroup[],
  meetings: readonly Meeting[],
  meetingStartMessageIndices: ReadonlyMap<string, number>,
): Map<number, string> {
  const anchors = new Map<number, string>();
  const meetingsByRunId = new Map(
    meetings.map((meeting) => [meeting.runId, meeting]),
  );

  for (const index of meetingAnchorIndices(groups)) {
    const runId = groups[index]?.runId;
    if (runId && meetingsByRunId.has(runId)) {
      anchors.set(index, runId);
    }
  }

  const anchoredRunIds = new Set(anchors.values());
  for (const meeting of meetings) {
    const hasCompletedChair = meeting.seats.some(
      (seat) =>
        seat.councilSeat?.role === "chair" && seat.status === "completed",
    );
    if (
      meeting.isRunning ||
      !hasCompletedChair ||
      anchoredRunIds.has(meeting.runId)
    ) {
      continue;
    }
    const startIndex = meetingStartMessageIndices.get(meeting.runId);
    if (startIndex === undefined || !meeting.stage) {
      continue;
    }
    const resultIndex = groups.findIndex(
      (group, index) =>
        !anchors.has(index) &&
        group.type === "assistant:present-files" &&
        group.stage === meeting.stage &&
        group.firstMessageIndex !== undefined &&
        group.firstMessageIndex > startIndex,
    );
    if (resultIndex >= 0) {
      anchors.set(resultIndex, meeting.runId);
      anchoredRunIds.add(meeting.runId);
    }
  }

  return anchors;
}

/**
 * Every meeting in the conversation, ordered by first appearance.
 *
 * First appearance rather than run id or timestamp: the caller renders each
 * meeting inside its own run's message group, so this order only has to agree
 * with the order the seats arrived in — which is the order the transcript
 * already shows.
 *
 * A seat carrying no run id is kept under an empty-string bucket rather than
 * dropped. It cannot be anchored to a message group, so it renders unanchored,
 * which is worse than being in the right place and much better than vanishing —
 * silently losing evidence that a meeting happened is the failure this module
 * exists to end.
 */
export function meetingsByRun(tasks: readonly Subtask[]): Meeting[] {
  const byRun = new Map<string, Subtask[]>();
  for (const task of tasks) {
    if (!task.councilSeat) {
      continue;
    }
    const runId = task.runId ?? "";
    const seats = byRun.get(runId);
    if (seats) {
      seats.push(task);
    } else {
      byRun.set(runId, [task]);
    }
  }

  return [...byRun.entries()].map(([runId, seats]) => {
    const rounds = debateRounds(seats);
    const ordered = rounds.flatMap((round) => round.seats);
    const reported = ordered.filter(
      (task) => task.status !== "in_progress",
    ).length;
    return {
      runId,
      stage: meetingStage(ordered),
      rounds,
      seats: ordered,
      reported,
      total: ordered.length,
      isRunning: reported < ordered.length,
    };
  });
}

/** The stage its seats agree on, or undefined when they do not. */
function meetingStage(seats: readonly Subtask[]): string | undefined {
  const stages = new Set(
    seats
      .map((task) => task.councilSeat?.stage)
      .filter((stage): stage is string => typeof stage === "string" && !!stage),
  );
  return stages.size === 1 ? [...stages][0] : undefined;
}

/** The meetings anchored to one run, for rendering inside its message group. */
export function meetingsForRun(
  meetings: readonly Meeting[],
  runId: string | undefined,
): Meeting[] {
  if (!runId) {
    return [];
  }
  return meetings.filter((meeting) => meeting.runId === runId);
}

/**
 * Live meetings no message group has claimed yet.
 *
 * A meeting whose run has no group yet is the live case: the seats stream
 * before the run's first message lands. Rendering those at the tail is what
 * keeps a meeting visible while it argues. A completed historical meeting with
 * an unloaded transcript anchor must stay hidden until that history page is
 * loaded; appending it after a newer turn presents old evidence as new work.
 * Seats with no run id remain visible because no history page can ever anchor
 * them.
 */
export function unanchoredMeetings(
  meetings: readonly Meeting[],
  anchoredRunIds: ReadonlySet<string>,
): Meeting[] {
  return meetings.filter(
    (meeting) =>
      !anchoredRunIds.has(meeting.runId) &&
      (meeting.isRunning || meeting.runId === ""),
  );
}
