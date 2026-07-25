import { Navigate, Route, Routes } from "react-router-dom";
import Join from "./routes/Join";
import Worker from "./routes/Worker";

// /host is David's route (frontend/src/routes/Host.tsx) — not built yet, out of
// scope here. Nikki's worker-facing routes are wired below.
export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/join" replace />} />
      <Route path="/join" element={<Join />} />
      <Route path="/worker/:id" element={<Worker />} />
    </Routes>
  );
}
