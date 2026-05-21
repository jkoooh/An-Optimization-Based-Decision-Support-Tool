import webbrowser
import folium
import pandas as pd
import searoute as sr
import pickle
from pathlib import Path

_GDF = None

def load_gdf():
    global _GDF
    if _GDF is None:
        try:
            from .ports_data import read_gdf
        except ImportError:
            from ports_data import read_gdf
        gdf = read_gdf()
        if gdf.index.name != "portid":
            gdf = gdf.set_index("portid", drop=False)
        _GDF = gdf
    return _GDF

def load_or_build_clusters(cache_path: str = "Data/clusters.pkl"):
    cache = Path(cache_path)
    if cache.exists():
        with cache.open("rb") as f:
            return pickle.load(f)
    try:
        from .clustering import assign_ports_to_centroids, build_clusters, country_port_centroids
    except ImportError:
        from clustering import assign_ports_to_centroids, build_clusters, country_port_centroids
    gdf = load_gdf()
    df = assign_ports_to_centroids(gdf, country_port_centroids)
    clusters = build_clusters(df)
    clusters = clusters[clusters["Cluster_id"].notna()].copy()
    with cache.open("wb") as f:
        pickle.dump(clusters, f)
    clusters["country"] = clusters["country"].replace({"Vietnam": "Viet Nam"})
    clusters["country"] = clusters["country"].replace({"Hong Kong SAR": "China, Hong Kong SAR"})
    return clusters

# Helper function for extracting df rows
def get_port(portid: str):
    """
    Helper function that finds port in dataframe based on portid.
    Returns the df row for that portid. 
    """
    gdf = load_gdf()
    try:
        return gdf.loc[portid]
    except KeyError as e:
        raise ValueError(f"Found no rows with={portid!r}") from e

# Helper function that swaps [lon, lat] to [lat, lon]
def lonlat_to_latlon(coords):
    """
    Helper function that swaps [lon, lat] to [lat, lon].
    """
    return [[lat, lon] for lon, lat in coords]

# Use searoute to builds a route length matrix
def build_route_length_matrix(gdf, filepath="Data/route_lengths.xlsx",
    routes_path="Data/routes.pkl",
    return_routes=False,
):
    '''
    Use searoute to builds a route length matrix from all ports 
    and to all ports in gdf.
    Writes the matrix to "Data/route_lengths.xlsx" and returns a dataframe matrix. 
    '''
    # Extract portid and coordinates from gdf
    ports = gdf[["Cluster_id", "lat", "lon"]].drop_duplicates().set_index("Cluster_id")

    # Helper function that runs searoute from a to b and return the length in nautical miles.
    def route_length_nm(a, b):
        route = sr.searoute((a["lon"], a["lat"]), (b["lon"], b["lat"]), units="naut", speed_knot=12)
        return route.properties["length"], route

    # Initiate the matrix with portids as index and columns
    matrix = pd.DataFrame(index=ports.index, columns=ports.index, dtype=float)
    routes = {}

    # Build the length matrix for all ports, and save each route in routes
    for i, (pid_a, a) in enumerate(ports.iterrows()):
        matrix.loc[pid_a, pid_a] = 0.0
        for pid_b, b in ports.iloc[i + 1 :].iterrows():

            try:
                dist, route = route_length_nm(a, b)
            except ValueError as e:
                raise ValueError(
                    "Invalid lon/lat for route. "
                    f"from Cluster_id={pid_a!r} (lat={a['lat']}, lon={a['lon']}) "
                    f"to Cluster_id={pid_b!r} (lat={b['lat']}, lon={b['lon']})"
                ) from e

            matrix.loc[pid_a, pid_b] = dist
            matrix.loc[pid_b, pid_a] = dist

            routes[(pid_a, pid_b)] = route
            routes[(pid_b, pid_a)] = route

    # Write the matrix to the Excel file and return
    matrix.to_excel(filepath)

    with open(routes_path, "wb") as f:
        pickle.dump(routes, f)

    return matrix, routes

# Plot routes on Folium map
def plot_routes(routes, out_html="routes.html"):
    '''
    Plots all routes in routes on Folium map.
    '''
    # Initialize map instance
    m = folium.Map(location=[0, 0], zoom_start=2, tiles="cartodb positron")

    # Add all routes to map
    for coords in routes:
        folium.PolyLine(
            locations=lonlat_to_latlon(coords),
            color="red",
            weight=5,
            opacity=1,
            tooltip="Sea route",
        ).add_to(m)

    # Write map to html and open
    m.save(out_html)
    webbrowser.open(out_html)

def plot_routes_between_countries(
    country_a: str,
    country_b: str,
    clusters_df,
    routes_path: str = "Data/routes.pkl",
    out_html: str | None = None,
):
    """
    Plot all routes between clusters in country_a and country_b using routes.pkl.
    """
    if clusters_df.empty:
        raise ValueError("clusters_df is empty")

    a_ids = set(clusters_df.loc[clusters_df["country"] == country_a, "Cluster_id"].dropna())
    b_ids = set(clusters_df.loc[clusters_df["country"] == country_b, "Cluster_id"].dropna())

    if not a_ids:
        raise ValueError(f"No clusters found for country_a={country_a!r}")
    if not b_ids:
        raise ValueError(f"No clusters found for country_b={country_b!r}")

    with open(routes_path, "rb") as f:
        routes = pickle.load(f)

    coords_list = []
    seen_pairs = set()
    for (pid_a, pid_b), route in routes.items():
        if pid_a in a_ids and pid_b in b_ids:
            key = tuple(sorted((pid_a, pid_b)))
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            coords_list.append(route.geometry["coordinates"])

    if not coords_list:
        raise ValueError(
            f"No routes found between {country_a!r} and {country_b!r} in {routes_path!r}"
        )

    if out_html is None:
        safe_a = "".join(ch if ch.isalnum() else "_" for ch in country_a)
        safe_b = "".join(ch if ch.isalnum() else "_" for ch in country_b)
        out_html = f"routes_{safe_a}_to_{safe_b}.html"

    plot_routes(coords_list, out_html=out_html)

def shortest_route_to_country(
    cluster_id,
    country: str,
    clusters_df=None,
    routes_path: str = "Data/routes.pkl",
    plot_route: bool = False,
):
    """
    Return the shortest route (length, route) from cluster_id to any cluster in country.
    If plot_route is True, plots the route on a Folium map.
    """

    # Check if cluster_df is provided, else load/build it
    if clusters_df is None:
        clusters_df = load_or_build_clusters()
    if clusters_df.empty:
        raise ValueError("clusters_df is empty")

    # Create a set of target cluster IDs in the specified country
    target_ids = set(clusters_df.loc[clusters_df["country"] == country, "Cluster_id"].dropna())
    if not target_ids:
        raise ValueError(f"No clusters found for country={country!r}")

    with open(routes_path, "rb") as f:
        routes = pickle.load(f)

    # Find the shortest route to any target cluster
    best = None  # (length, route, target_id)
    for target_id in target_ids:
        route = routes.get((cluster_id, target_id))
        if route is None:
            continue
        length = route.properties["length"]
        if best is None or length < best[0]:
            best = (length, route, target_id)
    # 
    if best is None:
        raise ValueError(
            f"No route found from cluster_id={cluster_id!r} to country={country!r} "
            f"in {routes_path!r}"
        )

    length, route, target_id = best
    if plot_route:
        plot_routes([route.geometry["coordinates"]], out_html="shortest_route.html")

    return length, route

def get_route(routes_path, portid_a, portid_b):
    with open(routes_path, "rb") as f:
        routes = pickle.load(f)
        try:
            return routes[(portid_a, portid_b)]
        except KeyError as e:
            raise KeyError(f"No route for ({portid_a!r}, {portid_b!r})") from e
    
def test_routing(start_id, end_id, speed_knot=12):
    start = get_port(start_id)
    end = get_port(end_id)

    route = sr.searoute(
        (float(start["lon"]), float(start["lat"])),
        (float(end["lon"]), float(end["lat"])),
        units="naut",
        speed_knot=speed_knot,
    )

    coords = route.geometry["coordinates"]

    print("Start lat:", start["lat"])
    print("End:", end_id, "lat:", end["lat"])
    print("Duration (hours):", route.properties["duration_hours"])
    print("Length (nautical miles):", route.properties["length"])

    plot_routes([coords])

if __name__ == "__main__":
    clusters = load_or_build_clusters()
    # matrix = build_route_length_matrix(clusters)
    # route = get_route("Data/routes.pkl", "port184", "port280")
    #plot_routes_between_countries("Norway", "United States", clusters)
    length, route = shortest_route_to_country("port474", "Vietnam", plot_route=False)
    print(length)

