import './style.css';
import './notes';
import('./viewer').catch(error => {
  const root = document.getElementById('root')!;
  root.textContent = `T2 map viewer unavailable: ${error.message}. `;
  const back = document.createElement('a'); back.href = '/'; back.textContent = 'Back to models'; root.append(back);
});
