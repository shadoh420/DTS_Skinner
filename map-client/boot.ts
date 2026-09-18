import './style.css';
import('./viewer').catch(error => {
  const root = document.getElementById('root')!;
  root.textContent = `T2 map viewer unavailable: ${error.message}. Install the local map data pack and reopen Maps. `;
  const back = document.createElement('a'); back.href = '/'; back.textContent = 'Back to models'; root.append(back);
});
