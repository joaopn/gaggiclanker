import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { boilerTemperature, DeviceStatusPill } from "@/components/DeviceStatusPill";
import { TooltipProvider } from "@/components/ui/tooltip";
import { createTestQueryClient } from "@/test/renderWithQueryClient";

const { getHealth, getDeviceStatus } = vi.hoisted(() => ({
  getHealth: vi.fn(),
  getDeviceStatus: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getHealth,
  getDeviceStatus,
}));

function renderPill() {
  return render(
    <QueryClientProvider client={createTestQueryClient()}>
      <MemoryRouter>
        <TooltipProvider>
          {/* The pill is a link to the device page, so it needs a router. */}
          <DeviceStatusPill />
        </TooltipProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const pill = () => screen.getByTestId("device-status-pill");

describe("DeviceStatusPill", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getHealth.mockResolvedValue({ status: "ok", version: "0.1.0", database: "ok" });
    getDeviceStatus.mockResolvedValue({
      configured: false,
      connected: false,
      host: null,
      identity: null,
      last_status: null,
    });
  });

  it("reports the backend first: nothing else is knowable without it", async () => {
    getHealth.mockRejectedValue(new Error("Failed to fetch"));
    getDeviceStatus.mockResolvedValue({ configured: true, connected: true, host: "gaggimate" });
    renderPill();
    await waitFor(() => expect(pill()).toHaveAttribute("data-state", "bad"));
    expect(pill()).toHaveTextContent("Offline");
  });

  it("is its dot alone below xl, with the words kept for a screen reader", async () => {
    renderPill();
    await waitFor(() => expect(pill()).toHaveAttribute("data-state", "good"));
    // The header has to fit at 360 px with Sync, Writes, Flush and Sign out beside it.
    expect(within(pill()).getByText("Online")).toHaveClass("max-xl:sr-only");
  });

  it("names the board when the machine is connected", async () => {
    getDeviceStatus.mockResolvedValue({
      configured: true,
      connected: true,
      host: "192.168.1.50",
      identity: { hardware: "GaggiMate Pro Rev 1.1", displayVersion: "v1.9.0" },
      last_status: null,
    });
    renderPill();
    await waitFor(() => expect(pill()).toHaveTextContent("GaggiMate Pro Rev 1.1"));
    expect(pill()).toHaveAttribute("data-state", "good");
  });

  it("says where it goes, since the sidebar no longer does", async () => {
    renderPill();
    await waitFor(() => expect(pill()).toHaveAttribute("data-state", "good"));
    // The device page has no nav entry; this pill is the way in, so the link
    // has to name its destination for anyone who cannot see a tooltip.
    expect(pill()).toHaveAttribute("href", "/device");
    expect(pill()).toHaveTextContent("Open the device page");
  });

  it("goes red when the machine is configured but not answering", async () => {
    getDeviceStatus.mockResolvedValue({
      configured: true,
      connected: false,
      host: "192.168.1.50",
      identity: null,
      last_status: null,
    });
    renderPill();
    await waitFor(() => expect(pill()).toHaveAttribute("data-state", "bad"));
    expect(pill()).toHaveTextContent("Machine offline");
  });

  it("shows the boiler temperature while connected, at every width", async () => {
    getDeviceStatus.mockResolvedValue({
      configured: true,
      connected: true,
      host: "192.168.1.50",
      identity: { hardware: "GaggiMate Pro Rev 1.1", displayVersion: "v1.9.0" },
      last_status: { m: 1, ct: 92.6, tt: 93 },
    });
    renderPill();
    const temperature = await within(pill()).findByTestId("device-temperature");
    // "93 °C", shortened to "93°" below sm; a screen reader hears "93 °C" either way.
    expect(temperature).toHaveTextContent(/^boiler at 93 °C°$/);
    expect(within(temperature).getByText("°C")).toHaveClass("max-sm:sr-only");
    expect(within(temperature).getByText("°")).toHaveClass("sm:hidden");
    // Not hidden below xl like the board's name: the dot and the reading are what fit.
    expect(temperature).not.toHaveClass("max-xl:sr-only");
    expect(temperature).not.toHaveClass("sr-only");
    expect(pill()).toHaveAccessibleName(/^GaggiMate Pro Rev 1\.1 boiler at 93 °C /);
  });

  it("shows no temperature before the machine has reported one", async () => {
    getDeviceStatus.mockResolvedValue({
      configured: true,
      connected: true,
      host: "192.168.1.50",
      identity: { hardware: "GaggiMate Pro Rev 1.1" },
      last_status: { m: 1 },
    });
    renderPill();
    await waitFor(() => expect(pill()).toHaveTextContent("GaggiMate Pro Rev 1.1"));
    expect(within(pill()).queryByTestId("device-temperature")).toBeNull();
  });

  it("shows no temperature once the machine is offline, even with a last reading kept", async () => {
    getDeviceStatus.mockResolvedValue({
      configured: true,
      connected: false,
      host: "192.168.1.50",
      identity: null,
      last_status: { ct: 92.4 },
    });
    renderPill();
    await waitFor(() => expect(pill()).toHaveTextContent("Machine offline"));
    expect(within(pill()).queryByTestId("device-temperature")).toBeNull();
    expect(pill()).not.toHaveTextContent("°C");
  });
});

describe("boilerTemperature", () => {
  it("rounds to whole degrees and refuses anything that is not a reading", () => {
    expect(boilerTemperature(92.4)).toBe(92);
    expect(boilerTemperature(92.5)).toBe(93);
    expect(boilerTemperature(0)).toBe(0);
    expect(boilerTemperature(null)).toBeNull();
    expect(boilerTemperature(undefined)).toBeNull();
    expect(boilerTemperature("92")).toBeNull();
    expect(boilerTemperature(Number.NaN)).toBeNull();
  });
});
