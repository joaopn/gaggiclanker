import { screen } from "@testing-library/react";
import { Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { DeviceStatusData, SyncStatusData } from "@/api/types";
import { DevicePage, SyncRedirect } from "@/pages/DevicePage";
import { boardView } from "@/test/boardFixtures";
import { renderWithQueryClient } from "@/test/renderWithQueryClient";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const { getDeviceStatus, getSyncStatus, getDeviceWrites, getProfileBoard } = vi.hoisted(() => ({
  getDeviceStatus: vi.fn(),
  getSyncStatus: vi.fn(),
  getDeviceWrites: vi.fn(),
  getProfileBoard: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getDeviceStatus,
  getSyncStatus,
  getDeviceWrites,
  getProfileBoard,
}));

function deviceStatus(overrides: Partial<DeviceStatusData> = {}): DeviceStatusData {
  return {
    configured: true,
    connected: true,
    host: "gaggimate.local",
    identity: {
      hardware: "GaggiMate Pro",
      displayVersion: "1.8.2",
      controllerVersion: "1.8.2",
      latestVersion: "1.8.3",
      channel: "latest",
      updating: false,
      spiffsTotal: 1_048_576,
      spiffsUsed: 786_432,
      sdTotal: 0,
    },
    last_status: null,
    ...overrides,
  };
}

/** Where a redirect landed, including the anchor. */
function Landed() {
  const { pathname, hash } = useLocation();
  return <p data-testid="landed">{`${pathname}${hash}`}</p>;
}

function renderAt(path: string) {
  return renderWithQueryClient(
    <Routes>
      <Route
        path="/device"
        element={
          <>
            <Landed />
            <DevicePage />
          </>
        }
      />
      <Route path="/sync" element={<SyncRedirect />} />
    </Routes>,
    { initialEntries: [path] },
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  getDeviceStatus.mockResolvedValue(deviceStatus());
  getSyncStatus.mockResolvedValue({
    configured: true,
    connected: true,
    running: false,
    last_runs: {},
    last_error: null,
    counts: null,
    recent_events: [],
  } as unknown as SyncStatusData);
  getDeviceWrites.mockResolvedValue({ enabled: false, items: [] });
  getProfileBoard.mockResolvedValue(boardView({ writes_enabled: false }));
});

describe("DevicePage", () => {
  it("names the board, the versions and the connection", async () => {
    renderAt("/device");

    expect(await screen.findByRole("heading", { name: "GaggiMate Pro" })).toBeInTheDocument();
    expect(screen.getByText("gaggimate.local · connected")).toBeInTheDocument();
    expect(screen.getByTestId("device-versions")).toHaveTextContent("1.8.3");
    expect(screen.getByTestId("device-connection")).toHaveTextContent("connected");
  });

  it("holds the sync cards under the facts, and no link to a Sync page", async () => {
    const { container } = renderAt("/device");
    await screen.findByRole("heading", { name: "GaggiMate Pro" });

    // The Sync button is the top bar's; the cards here are what syncs did.
    for (const anchor of ["sync", "board", "writes"]) {
      expect(container.querySelector(`#${anchor}`)).not.toBeNull();
    }
    expect(screen.queryByRole("link", { name: /Sync/ })).not.toBeInTheDocument();
    expect(container.querySelector('a[href="/sync"]')).toBeNull();
  });

  it("has no telemetry card, because the machine's own UI has one", async () => {
    renderAt("/device");
    await screen.findByRole("heading", { name: "GaggiMate Pro" });

    expect(screen.queryByTestId("device-telemetry")).not.toBeInTheDocument();
    expect(screen.queryByTestId("device-warnings")).not.toBeInTheDocument();
  });

  it.each([
    ["/sync", "/device"],
    ["/sync#sync", "/device#sync"],
    ["/sync#pull", "/device#pull"],
    ["/sync#board", "/device#board"],
    ["/sync#writes", "/device#writes"],
  ])("sends the old Sync page's %s here", async (from, target) => {
    renderAt(from);
    expect(await screen.findByTestId("landed")).toHaveTextContent(target);
  });

  it("treats 'no machine configured' as a setup step, not a fault", async () => {
    getDeviceStatus.mockResolvedValue(
      deviceStatus({ configured: false, connected: false, host: null, identity: null }),
    );

    renderAt("/device");

    expect(await screen.findByText("No machine configured")).toBeInTheDocument();
    expect(screen.getByText(/gaggimateHost/)).toBeInTheDocument();
  });
});
