import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";

// Discard the credential format used by early development builds. Core now
// hands the page a short-lived opaque browser session for this tab only.
window.localStorage.removeItem("atc.adminToken");

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
