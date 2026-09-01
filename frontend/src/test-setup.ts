import { vi } from "vitest";
import "@testing-library/jest-dom/vitest";

// Mock ResizeObserver for recharts in jsdom
const mockResizeObserver = vi.fn().mockImplementation(() => ({
  observe: vi.fn(),
  unobserve: vi.fn(),
  disconnect: vi.fn(),
}));

Object.defineProperty(window, "ResizeObserver", {
  value: mockResizeObserver,
});
