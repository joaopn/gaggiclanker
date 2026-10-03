import { describe, expect, it } from "vitest";
import type { ChatThread, SetRow } from "@/api/types";
import {
  ARCHIVED,
  buildFolders,
  defaultFolderKey,
  GENERAL,
  latestSetId,
} from "@/components/chat/ConversationPicker";

function set(id: number, created_at: string, over: Partial<SetRow> = {}): SetRow {
  return { id, name: `Set ${id}`, created_at, current_version_label: "v1", ...over } as SetRow;
}

function thread(id: number, set_id: number | null): ChatThread {
  return { id, set_id, title: `t${id}`, updated_at: "2026-03-01T10:00:00.000Z" } as ChatThread;
}

describe("latestSetId", () => {
  it("is the Set made last, whatever order the list serves them in", () => {
    const sets = [
      set(1, "2026-01-01T00:00:00Z"),
      set(2, "2026-03-01T00:00:00Z"),
      set(3, "2026-02-01T00:00:00Z"),
    ];
    expect(latestSetId(sets)).toBe(2);
  });

  it("breaks a tie on the time by the higher id, the later insert", () => {
    expect(latestSetId([set(7, "2026-01-01T00:00:00Z"), set(5, "2026-01-01T00:00:00Z")])).toBe(7);
    expect(latestSetId([set(5, "2026-01-01T00:00:00Z"), set(7, "2026-01-01T00:00:00Z")])).toBe(7);
  });

  it("is null with no Sets", () => {
    expect(latestSetId([])).toBeNull();
  });
});

describe("defaultFolderKey", () => {
  const sets = [set(1, "2026-01-01T00:00:00Z"), set(2, "2026-03-01T00:00:00Z")];

  it("opens the selected conversation's badge first, then a link's, then the newest Set", () => {
    const folders = buildFolders([thread(10, 1), thread(11, 99)], sets);
    const pick = (selectedId: number | null, linkedSetId: number | null) =>
      defaultFolderKey({ folders, sets, selectedId, linkedSetId });

    expect(pick(10, 2)).toBe("set-1");
    // A conversation whose Set is gone opens Archived, where it is listed.
    expect(pick(11, null)).toBe(ARCHIVED);
    expect(pick(null, 1)).toBe("set-1");
    expect(pick(null, null)).toBe("set-2");
    // A link naming a Set with no badge falls through to the newest.
    expect(pick(null, 42)).toBe("set-2");
    // A selected id not in any list yet (just created) falls through too.
    expect(pick(77, null)).toBe("set-2");
  });

  it("is General when there are no Sets", () => {
    expect(
      defaultFolderKey({
        folders: buildFolders([], []),
        sets: [],
        selectedId: null,
        linkedSetId: null,
      }),
    ).toBe(GENERAL);
  });
});

describe("buildFolders", () => {
  it("puts the current version on a Set's badge, and none on a Set being designed", () => {
    const folders = buildFolders(
      [],
      [
        set(1, "2026-01-01T00:00:00Z", { current_version_label: "v1.2" }),
        set(2, "2026-02-01T00:00:00Z", { designing: true }),
      ],
    );
    expect(folders.map((folder) => folder.versionLabel)).toEqual([null, "v1.2", null]);
  });
});
