import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { WorkspaceQueryProvider } from "./queryClient";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <WorkspaceQueryProvider>
      <App />
    </WorkspaceQueryProvider>
  </React.StrictMode>
);
