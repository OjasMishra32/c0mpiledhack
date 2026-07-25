import { Navigate, Route, Routes } from 'react-router-dom';
import { Host } from './routes/Host';

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/host" replace />} />
      <Route path="/host" element={<Host />} />
    </Routes>
  );
}
