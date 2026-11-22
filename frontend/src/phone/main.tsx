import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource/ibm-plex-sans/400.css";
import "@fontsource/ibm-plex-sans/600.css";
import "@fontsource/ibm-plex-sans-condensed/600.css";
import { PhoneApp } from "./PhoneApp";
import "./phone.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <PhoneApp />
  </StrictMode>,
);
