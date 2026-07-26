import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App.js";
import "./dashboard.css";

const root = document.getElementById("dashboard-root");
if (!root) throw new Error("dashboard-root is missing");

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>
);
