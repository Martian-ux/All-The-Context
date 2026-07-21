import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { consumeSetupToken } from "./api";
import "./styles.css";

consumeSetupToken();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
