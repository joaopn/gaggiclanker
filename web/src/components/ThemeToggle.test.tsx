import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ThemeToggle } from "@/components/ThemeToggle";
import { DARK_THEME_ID, LIGHT_THEME_ID, THEME_STORAGE_KEY } from "@/lib/theme";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

describe("ThemeToggle", () => {
  it("cycles light -> dark -> system, restamping the document each time", async () => {
    const user = setupUser();
    renderWithQueryClient(<ThemeToggle />);

    const button = screen.getByTestId("theme-toggle");
    expect(button).toHaveAttribute("data-preference", "system");

    await user.click(button);
    expect(button).toHaveAttribute("data-preference", "light");
    expect(document.documentElement.getAttribute("data-theme")).toBe(LIGHT_THEME_ID);
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");

    await user.click(button);
    expect(button).toHaveAttribute("data-preference", "dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe(DARK_THEME_ID);
    expect(document.documentElement.classList.contains("dark")).toBe(true);

    await user.click(button);
    expect(button).toHaveAttribute("data-preference", "system");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBeNull();
  });
});
