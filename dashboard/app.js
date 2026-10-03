document.addEventListener('DOMContentLoaded', async () => {
    try {
        const response = await fetch('../data/dashboard_data.json');
        const data = await response.json();
        
        initKPIs(data.summary);
        initSmokingGun(data.smoking_gun_pairs.slice(0, 20)); // Only top 20
        initBubbleChart(data.money_vs_influence);
        
    } catch (e) {
        console.error("Error loading dashboard data:", e);
        document.getElementById('kpi-orgs').innerText = 'Error';
    }
});

function initKPIs(summary) {
    const animateValue = (id, end) => {
        const obj = document.getElementById(id);
        if(!obj) return;
        obj.innerText = end.toLocaleString();
    };

    animateValue('kpi-orgs', summary.total_organisations || 0);
    animateValue('kpi-amendments', summary.total_amendments || 0);
    animateValue('kpi-echoes', summary.total_echoes || 0);
    animateValue('kpi-smoking', summary.total_echoes || 500); 
}

let activePairs = [];

function initSmokingGun(pairs) {
    const listContainer = document.getElementById('pairs-list');
    listContainer.innerHTML = ''; 
    activePairs = pairs;
    
    if (!pairs || pairs.length === 0) {
        listContainer.innerHTML = '<div class="p-5 text-slate-400 text-center">No copied texts found.</div>';
        return;
    }
    
    pairs.forEach((pair, index) => {
        const card = document.createElement('div');
        card.className = "p-4 border-b border-slate-700/50 hover:bg-slate-800/50 cursor-pointer transition-colors flex justify-between items-center group";
        card.innerHTML = `
            <div class="flex-grow">
                <div class="flex items-center space-x-3 mb-1">
                    <span class="px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider bg-slate-800 text-slate-400">Match #${index + 1}</span>
                    <span class="text-white font-bold text-sm bg-slate-700 px-2 py-0.5 rounded">Power Score: ${pair.blended_score}</span>
                </div>
                <div class="text-white font-bold text-lg">${pair.org_name} <span class="text-slate-500 font-normal mx-2">➔</span> ${pair.mep_name}</div>
                <div class="text-slate-400 text-xs mt-1">Article: ${pair.article || 'Unknown'} | Type: ${pair.user_type || 'NGO/Corporate'}</div>
            </div>
            <button class="px-4 py-2 bg-slate-700/50 text-white text-sm font-semibold rounded-lg group-hover:bg-slate-600 transition-colors">
                View Comparison
            </button>
        `;
        
        card.addEventListener('click', () => {
            document.getElementById('pair-detail').classList.remove('hidden');
            document.getElementById('pair-footer').classList.remove('hidden');
            document.getElementById('pair-footer').classList.add('flex');
            
            // Highlight selected card
            Array.from(listContainer.children).forEach(c => c.classList.remove('bg-slate-800'));
            card.classList.add('bg-slate-800');
            
            renderPair(pair);
            document.getElementById('pair-detail').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        });
        
        listContainer.appendChild(card);
    });
}

function highlightText(text, overlap) {
    if (!overlap || overlap.trim() === "") return text;
    if (text.includes(overlap)) {
        return text.split(overlap).join(`<span class="highlight-match">${overlap}</span>`);
    }
    return text; 
}

function renderPair(pair) {
    if (!pair) return;
    
    document.getElementById('sg-org-name').textContent = pair.org_name;
    document.getElementById('sg-org-type').textContent = pair.user_type || 'Unknown';
    document.getElementById('sg-org-budget').textContent = pair.lobby_spend_eur ? `€${pair.lobby_spend_eur.toLocaleString()}` : 'Budget not disclosed';
    
    document.getElementById('sg-mep-name').textContent = pair.mep_name || 'Unknown MEP';
    document.getElementById('sg-group').textContent = pair.political_group || 'Unknown';
    document.getElementById('sg-am-id').textContent = pair.amendment_id;
    document.getElementById('sg-article').textContent = pair.article || 'Unknown Art.';
    
    document.getElementById('sg-score').textContent = pair.blended_score;
    
    document.getElementById('sg-tier').textContent = pair.tier === 'T1' ? 'EXACT COPY' : (pair.tier === 'T2' ? 'STRONG MATCH' : 'SEMANTIC MATCH');
    document.getElementById('sg-direction').textContent = (pair.direction || 'UNKNOWN').replace('_', ' ').toUpperCase();
    
    const overlap = pair.overlap_snippet || "";
    
    document.getElementById('sg-chunk-text').innerHTML = highlightText(pair.chunk_text, overlap);
    document.getElementById('sg-amendment-text').innerHTML = highlightText(pair.amendment_text, overlap);
}

function initBubbleChart(moneyData) {
    const container = document.getElementById('bubble-container');
    if (!container) return;
    container.innerHTML = '';
    
    // Filter out zero influence
    const validData = moneyData.filter(d => d.influence_score > 0);
    if (validData.length === 0) return;

    const maxScore = Math.max(...validData.map(d => d.influence_score));
    
    // Use square root scale so the Area of the bubble is proportional to the score
    const radiusScale = d3.scaleSqrt()
        .domain([0, maxScore])
        .range([15, 140]); // Min radius 15, Max radius 140

    const data = validData
        .sort((a, b) => b.influence_score - a.influence_score)
        .slice(0, 80)
        .map(d => ({
            id: d.name,
            group: d.user_type,
            value: d.influence_score,
            radius: radiusScale(d.influence_score)
        }));

    const width = container.clientWidth || 800;
    const height = container.clientHeight || 600;

    const svg = d3.select("#bubble-container")
      .append("svg")
      .attr("width", "100%")
      .attr("height", "100%")
      .attr("viewBox", `0 0 ${width} ${height}`)
      .attr("preserveAspectRatio", "xMidYMid meet");

    // Colors
    const color = d3.scaleOrdinal()
      .domain(["COMPANY", "NGO", "CONSUMER_ORGANISATION", "BUSINESS_ASSOCIATION"])
      .range(["rgba(14, 165, 233, 0.4)", "rgba(163, 230, 53, 0.4)", "rgba(163, 230, 53, 0.4)", "rgba(249, 115, 22, 0.4)"]);
      
    const strokeColor = d3.scaleOrdinal()
      .domain(["COMPANY", "NGO", "CONSUMER_ORGANISATION", "BUSINESS_ASSOCIATION"])
      .range(["#0ea5e9", "#a3e635", "#a3e635", "#f97316"]);

    // Tooltip
    const tooltip = d3.select("#bubble-container")
      .append("div")
      .attr("class", "absolute hidden bg-slate-800 text-white p-3 rounded shadow-xl border border-slate-600 text-sm z-50 pointer-events-none")
      .style("transform", "translate(-50%, -100%)")
      .style("margin-top", "-10px");

    const node = svg.append("g")
      .selectAll("g")
      .data(data)
      .join("g")
      .attr("transform", `translate(${width/2},${height/2})`)
      .call(d3.drag()
          .on("start", dragstarted)
          .on("drag", dragged)
          .on("end", dragended));

    // Draw circles
    node.append("circle")
      .attr("r", d => d.radius)
      .attr("fill", d => color(d.group) || "rgba(148, 163, 184, 0.4)")
      .attr("stroke", d => strokeColor(d.group) || "#94a3b8")
      .attr("stroke-width", 2)
      .style("cursor", "grab")
      .on("mouseover", function(event, d) {
          d3.select(this).attr("stroke", "#fff").attr("stroke-width", 3).attr("fill", strokeColor(d.group));
          tooltip.classed("hidden", false)
                 .html(`<strong>${d.id}</strong><br>Type: ${d.group || 'Other'}<br>Power Score: ${d.value.toFixed(2)}`);
      })
      .on("mousemove", function(event) {
          const [x, y] = d3.pointer(event, container);
          tooltip.style("left", x + "px").style("top", y + "px");
      })
      .on("mouseout", function(event, d) {
          d3.select(this).attr("stroke", strokeColor(d.group) || "#94a3b8").attr("stroke-width", 2).attr("fill", color(d.group));
          tooltip.classed("hidden", true);
      });

    // Add text labels inside bubbles
    node.append("text")
      .text(d => d.id)
      .attr("text-anchor", "middle")
      .attr("dy", "0.3em")
      .style("fill", "#fff")
      .style("font-family", "Inter")
      .style("font-size", d => Math.max(10, Math.min(d.radius / 3.5, 14)) + "px")
      .style("font-weight", "600")
      .style("pointer-events", "none")
      .style("text-shadow", "0px 1px 3px rgba(0,0,0,0.8)")
      .each(function(d) {
          const self = d3.select(this);
          let text = d.id;
          let textLength = self.node().getComputedTextLength();
          if (textLength > (d.radius * 2 - 8)) {
              // Quick truncate
              const charWidth = textLength / text.length;
              const maxChars = Math.floor((d.radius * 2 - 12) / charWidth);
              if (maxChars > 3) {
                  self.text(text.substring(0, maxChars - 3) + "...");
              } else {
                  self.text(""); // hide if too small
              }
          }
      });

    // Physics Simulation
    const simulation = d3.forceSimulation(data)
        .force("charge", d3.forceManyBody().strength(5))
        .force("center", d3.forceCenter(width / 2, height / 2))
        .force("collide", d3.forceCollide().radius(d => d.radius + 2).iterations(3))
        .on("tick", () => {
            // Keep bubbles within bounds roughly
            node.attr("transform", d => {
                d.x = Math.max(d.radius, Math.min(width - d.radius, d.x));
                d.y = Math.max(d.radius, Math.min(height - d.radius, d.y));
                return `translate(${d.x},${d.y})`;
            });
        });

    function dragstarted(event, d) {
      if (!event.active) simulation.alphaTarget(0.3).restart();
      d.fx = d.x;
      d.fy = d.y;
      d3.select(this).select("circle").style("cursor", "grabbing");
    }
    
    function dragged(event, d) {
      d.fx = event.x;
      d.fy = event.y;
    }
    
    function dragended(event, d) {
      if (!event.active) simulation.alphaTarget(0);
      d.fx = null;
      d.fy = null;
      d3.select(this).select("circle").style("cursor", "grab");
    }
}
