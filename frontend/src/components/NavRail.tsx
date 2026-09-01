import { NavLink } from "react-router-dom";
import { navEntries } from "../routes";

export function NavRail() {
  return (
    <nav style={{ width: 160, borderRight: "1px solid var(--hairline)", padding: "16px 0" }}>
      {navEntries.map((entry) => (
        <NavLink
          key={entry.navPath}
          to={entry.navPath}
          style={({ isActive }) => ({
            display: "block", padding: "8px 20px", fontFamily: "var(--font-display)",
            textTransform: "uppercase", fontSize: 12, letterSpacing: "0.06em",
            color: isActive ? "var(--amber)" : "var(--ink)",
            borderLeft: isActive ? "2px solid var(--amber)" : "2px solid transparent",
          })}
        >
          {entry.navLabel}
        </NavLink>
      ))}
    </nav>
  );
}
