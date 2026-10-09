import { describe, expect, it } from "vitest";
import { CHAT_POSITION_KEY, loadChatPosition, saveChatPosition } from "@/lib/chatPosition";

function memory(): Storage {
  const values = new Map<string, string>();
  return {
    get length() {
      return values.size;
    },
    clear: () => values.clear(),
    getItem: (key) => values.get(key) ?? null,
    key: (index) => [...values.keys()][index] ?? null,
    removeItem: (key) => void values.delete(key),
    setItem: (key, value) => void values.set(key, value),
  };
}

describe("Where the person was on the Chat page", () => {
  it("is nowhere until something is saved, and reads back what was", () => {
    const storage = memory();
    expect(loadChatPosition(storage)).toEqual({ thread: null, folder: null });
    saveChatPosition({ thread: 7, folder: "set-3" }, storage);
    expect(loadChatPosition(storage)).toEqual({ thread: 7, folder: "set-3" });
    saveChatPosition({ thread: null, folder: "general" }, storage);
    expect(loadChatPosition(storage)).toEqual({ thread: null, folder: "general" });
  });

  it("drops each bad field on its own", () => {
    const storage = memory();
    for (const [raw, wanted] of [
      ["not json", { thread: null, folder: null }],
      ["42", { thread: null, folder: null }],
      ["null", { thread: null, folder: null }],
      ['{"thread":"7","folder":"set-3"}', { thread: null, folder: "set-3" }],
      ['{"thread":1.5,"folder":3}', { thread: null, folder: null }],
      ['{"thread":-2,"folder":""}', { thread: null, folder: null }],
      ['{"thread":9}', { thread: 9, folder: null }],
    ] as const) {
      storage.setItem(CHAT_POSITION_KEY, raw);
      expect(loadChatPosition(storage), raw).toEqual(wanted);
    }
  });

  it("opens as before when storage throws or is missing", () => {
    const broken = {
      ...memory(),
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
    } as Storage;
    expect(loadChatPosition(broken)).toEqual({ thread: null, folder: null });
    expect(() => saveChatPosition({ thread: 1, folder: null }, broken)).not.toThrow();
    expect(loadChatPosition(undefined)).toEqual({ thread: null, folder: null });
  });
});
