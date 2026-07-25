import { Navigate, Route, Routes } from 'react-router-dom';
import { Host } from './routes/Host';
import Join from './routes/Join';
import Worker from './routes/Worker';

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/host" replace />} />
      <Route path="/host" element={<Host />} />
      <Route path="/join" element={<Join />} />
      <Route path="/worker/:id" element={<Worker />} />
    </Routes>
  );
}
