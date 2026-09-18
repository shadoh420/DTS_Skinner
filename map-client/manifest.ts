const response = await fetch('/t2-map-data/manifest.json');
if (!response.ok) throw new Error('Local T2 map data is missing');
export default await response.json();
