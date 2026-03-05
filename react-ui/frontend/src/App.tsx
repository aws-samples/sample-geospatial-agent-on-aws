import { BrowserRouter as Router, Routes, Route, useNavigate } from 'react-router-dom';
import { Navigation } from './components/Navigation';
import { Chat } from './pages/Chat';
import { UseCaseGallery } from './pages/UseCaseGallery';
import { Login } from './components/Login';
import { ProtectedRoute } from './components/ProtectedRoute';
import { Technology } from './pages/Technology';
import './App.css';

function LoginPage() {
  const navigate = useNavigate();

  const handleLoginSuccess = () => {
    navigate('/');
  };

  return <Login onLoginSuccess={handleLoginSuccess} />;
}

function App() {
  return (
    <Router>
      <Routes>
        {/* Login Route - Public */}
        <Route path="/login" element={<LoginPage />} />

        {/* Protected Routes */}
        <Route
          path="/*"
          element={
            <ProtectedRoute>
              <div style={{ height: '100vh', display: 'flex', flexDirection: 'column' }}>
                <Navigation />
                <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
                  <Routes>
                    <Route path="/" element={<Chat />} />
                    <Route path="/use-cases" element={<UseCaseGallery />} />
                    <Route path="/chat/:scenarioId" element={<Chat />} />
                    <Route path="/technology" element={<Technology />} />
                  </Routes>
                </div>
              </div>
            </ProtectedRoute>
          }
        />
      </Routes>
    </Router>
  );
}

export default App;
