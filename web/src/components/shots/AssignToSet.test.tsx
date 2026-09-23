import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AssignToSet } from "@/components/shots/AssignToSet";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";
import { judgement, setRow, version } from "@/test/setsFixtures";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const { getSets, putShotSetVersion, addSetVersion } = vi.hoisted(() => ({
  getSets: vi.fn(),
  putShotSetVersion: vi.fn(),
  addSetVersion: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getSets,
  putShotSetVersion,
  addSetVersion,
}));

beforeEach(() => {
  vi.clearAllMocks();
  getSets.mockResolvedValue({ items: [setRow()] });
  putShotSetVersion.mockResolvedValue({ set_badge: null });
  addSetVersion.mockResolvedValue(version({ id: 23, version_no: 3, parent_version_id: 22 }));
});

describe("AssignToSet", () => {
  it("says plainly when a shot is in no Set, and preselects nothing", async () => {
    renderWithQueryClient(<AssignToSet shotId={9} setVersion={null} judgement={null} />);

    expect(await screen.findByTestId("assign-current")).toHaveTextContent("Not in a Set yet");
    // The matcher already filed everything it could tell apart, so a shot that
    // reached this form is one the archive had no answer for. Offering a Set
    // anyway would be a guess with somebody else's hand on it.
    await waitFor(() => expect(screen.getByLabelText("Assign to")).toHaveValue(""));
  });

  it("assigns only once the choice differs from what is already there", async () => {
    const user = setupUser();
    renderWithQueryClient(
      <AssignToSet shotId={9} setVersion={version({ id: 22, version_no: 2 })} judgement={null} />,
    );

    await waitFor(() => expect(screen.getByLabelText("Assign to")).toHaveValue("22"));
    expect(screen.getByRole("button", { name: "Assign" })).toBeDisabled();

    await user.selectOptions(screen.getByLabelText("Assign to"), "");
    await user.click(screen.getByRole("button", { name: "Assign" }));

    await waitFor(() => expect(putShotSetVersion).toHaveBeenCalled());
    expect(putShotSetVersion.mock.calls[0].slice(0, 2)).toEqual([9, null]);
  });

  it("copies the judgement's grind and doses into a new version, and files the shot under it", async () => {
    const user = setupUser();
    renderWithQueryClient(
      <AssignToSet
        shotId={9}
        setVersion={version({ id: 22, version_no: 2 })}
        judgement={judgement({ grind_setting: "20", dose_in_g: 18.5, dose_out_g: 37 })}
      />,
    );

    await user.click(await screen.findByRole("button", { name: /New version from this shot/ }));
    expect(screen.getByTestId("branch-copied")).toHaveTextContent("grind 20");
    await user.type(screen.getByLabelText("What are you trying?"), "a touch finer");
    await user.click(screen.getByRole("button", { name: "Create version" }));

    await waitFor(() => expect(addSetVersion).toHaveBeenCalled());
    expect(addSetVersion.mock.calls[0].slice(0, 2)).toEqual([
      3,
      {
        intent: "a touch finer",
        // Recording after the fact: nothing left to predict about this shot.
        prediction: "",
        origin: "manual",
        grind_setting: "20",
        // The number travels with the text (the shared `grindPatch` helper).
        // A version carrying only the text is a gap in the trend line nobody
        // attributes to a missing field.
        grind_value: 20,
        dose_g: 18.5,
        target_yield_g: 37,
      },
    ]);
    await waitFor(() => expect(putShotSetVersion).toHaveBeenCalled());
    expect(putShotSetVersion.mock.calls[0].slice(0, 2)).toEqual([9, 23]);
  });

  it("does not file the shot when the version could not be created", async () => {
    const user = setupUser();
    addSetVersion.mockRejectedValue(new Error("nope"));
    renderWithQueryClient(
      <AssignToSet
        shotId={9}
        setVersion={version({ id: 22 })}
        judgement={judgement({ grind_setting: "20" })}
      />,
    );

    await user.click(await screen.findByRole("button", { name: /New version from this shot/ }));
    await user.type(screen.getByLabelText("What are you trying?"), "a touch finer");
    await user.click(screen.getByRole("button", { name: "Create version" }));

    await waitFor(() => expect(addSetVersion).toHaveBeenCalled());
    // A shot filed under a version that was never created is a dangling
    // reference the server would refuse anyway.
    expect(putShotSetVersion).not.toHaveBeenCalled();
    // And the form stays open with the intent still in it.
    expect(screen.getByLabelText("What are you trying?")).toHaveValue("a touch finer");
  });

  it("says what will happen when the judgement records no recipe at all", async () => {
    const user = setupUser();
    renderWithQueryClient(
      <AssignToSet shotId={9} setVersion={version({ id: 22 })} judgement={null} />,
    );

    await user.click(await screen.findByRole("button", { name: /New version from this shot/ }));

    expect(screen.getByTestId("branch-copied")).toHaveTextContent(
      "inherits the old recipe unchanged",
    );
  });
});
