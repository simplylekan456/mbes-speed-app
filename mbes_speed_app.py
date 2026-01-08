# mbes_speed_app.py
import math
import streamlit as st

# ----------------- SONAR DEAD-TIME PRESETS -----------------
SONAR_DEAD_TIMES = {
    "R2Sonic (High Rate)": 0.02,               # ~10–30 ms
    "Norbit WBMS (High Rate)": 0.03,
    "Teledyne Reson T20/T50 (Shallow)": 0.04,
    "Kongsberg EM2040 (Shallow)": 0.05,
    "Teledyne/Simrad Shelf MBES": 0.10,
    "Kongsberg Deepwater EM302/304": 0.15,
    "Atlas Hydrosweep (Deepwater)": 0.20,
    "Generic Shallow MBES": 0.05,
    "Generic Deep MBES": 0.15,
    "Custom / Other Sonar": None,             # no preset
}

ORDER_COVERAGE = {
    "Custom (set coverage manually)": None,
    "IHO Order 2 (5% coverage)": 5.0,
    "IHO Order 1b (5% coverage)": 5.0,
    "IHO Order 1a (100% coverage)": 100.0,
    "IHO Special Order (100% coverage)": 100.0,
    "IHO Exclusive Order (200% coverage)": 200.0,
}

st.set_page_config(
    page_title="Maximum Planned Vessel Speed (IHO Orders + Sonar Dead Time)",
    layout="wide",
)

st.title("Maximum Planned Vessel Speed (IHO Orders + Sonar Dead Time)")

with st.sidebar:
    st.header("Survey & System Inputs")

    depth = st.number_input("Depth D (m)", value=100.0, min_value=0.0, step=1.0)
    swath_deg = st.number_input("Total Swath (deg)", value=120.0, min_value=0.0, max_value=179.9, step=1.0)
    beam_deg = st.number_input("Tx Beam Width φᵀ (deg)", value=1.5, min_value=0.01, max_value=179.9, step=0.1)
    sound_speed = st.number_input("Sound Speed c (m/s)", value=1500.0, min_value=0.0, step=1.0)

    st.markdown("---")
    st.header("IHO Coverage / Overlap")

    order_preset = st.selectbox(
        "IHO Survey Order Preset",
        list(ORDER_COVERAGE.keys()),
        index=0,
    )

    preset_cov = ORDER_COVERAGE[order_preset]
    if preset_cov is None:
        coverage_pct = st.number_input(
            "Bathymetric Coverage / Overlap (%)",
            value=200.0,
            min_value=0.01,
            step=1.0,
            help="Enter S-44-style coverage (e.g., 100%, 200%, etc.)",
        )
    else:
        coverage_pct = preset_cov
        st.number_input(
            "Bathymetric Coverage / Overlap (%)",
            value=float(preset_cov),
            disabled=True,
            help="Set by the selected IHO Order preset",
        )

    st.caption(
        f"Preset coverage for **{order_preset}** = **{coverage_pct:.1f}%**"
        if preset_cov is not None
        else "Custom coverage — you control the percentage."
    )

    st.markdown("---")
    st.header("Sonar & Dead Time")

    sonar_name = st.selectbox("Sonar System", list(SONAR_DEAD_TIMES.keys()), index=7)

    dead_mode = st.radio(
        "Dead Time Mode",
        ("Auto (use sonar preset)", "Manual (editable)"),
        index=0,
    )

    preset_dt = SONAR_DEAD_TIMES[sonar_name]

    if dead_mode == "Auto (use sonar preset)":
        if preset_dt is None:
            st.warning(
                "No preset dead-time for this sonar. "
                "Either switch to **Manual** or enter a custom sonar in the code."
            )
            dead_time = st.number_input("Dead Time Δt (s)", value=0.10, min_value=0.0, step=0.01)
        else:
            dead_time = preset_dt
            st.number_input(
                "Dead Time Δt (s)",
                value=float(preset_dt),
                disabled=True,
                help=f"Preset from sonar type: {sonar_name}",
            )
    else:
        default_dt = preset_dt if preset_dt is not None else 0.10
        dead_time = st.number_input(
            "Dead Time Δt (s)",
            value=float(default_dt),
            min_value=0.0,
            step=0.01,
        )

st.markdown("### Calculation")

run_calc = st.button("Calculate maximum vessel speed")

if run_calc:
    try:
        # -------- Sanity checks --------
        if depth <= 0 or sound_speed <= 0:
            raise ValueError("Depth and sound speed must be positive.")
        if not (0 < swath_deg < 180):
            raise ValueError("Swath angle must be between 0 and 180 degrees.")
        if beam_deg <= 0 or beam_deg >= 180:
            raise ValueError("Beam width must be between 0 and 180 degrees.")
        if coverage_pct <= 0:
            raise ValueError("Coverage must be greater than 0%.")
        if dead_time < 0:
            raise ValueError("Dead time cannot be negative.")

        # -------- Coverage → advance fraction --------
        C = coverage_pct / 100.0          # coverage factor
        advance_fraction = 1.0 / C        # fraction of footprint between pings
        advance_pct = advance_fraction * 100.0

        # -------- Core geometry & timing --------
        theta_deg = swath_deg / 2.0
        theta_rad = math.radians(theta_deg)
        beam_half_rad = math.radians(beam_deg / 2.0)

        # Slant range to outer beam
        R = depth / math.cos(theta_rad)

        # Two-way travel time
        t_2way = 2 * R / sound_speed

        # Ping cycle time
        T = t_2way + dead_time

        # Along-track footprint length
        L = 2 * depth * math.tan(beam_half_rad)

        # Allowed vessel advance per ping (m)
        d = advance_fraction * L

        # Speed
        v_ms = d / T
        v_knots = v_ms * 1.94384

        # ---- Top-line metrics ----
        c1, c2, c3 = st.columns(3)
        c1.metric("Max Speed (m/s)", f"{v_ms:.3f}")
        c2.metric("Max Speed (knots)", f"{v_knots:.2f}")
        c3.metric("Advance per Ping", f"{advance_pct:.1f}% of footprint")

        st.write(
            f"Allowed advance distance per ping **d ≈ {d:.3f} m** "
            f"for coverage **{coverage_pct:.1f}%**."
        )

        # ---- Detailed breakdown (matches original text panel) ----
        lines = []
        lines.append("=== INPUTS ===")
        lines.append(f"Depth D = {depth:.3f} m")
        lines.append(f"Total swath = {swath_deg:.3f}° (±{theta_deg:.3f}°)")
        lines.append(f"Tx beam width (along-track) φ_T = {beam_deg:.3f}°")
        lines.append(f"Sound speed c = {sound_speed:.3f} m/s")
        lines.append(f"Bathymetric coverage (S-44 style) = {coverage_pct:.1f}%")
        lines.append(f"→ Advance per ping ≈ {advance_pct:.1f}% of footprint")
        lines.append("")
        lines.append(f"Sonar system = {sonar_name}")
        lines.append(
            f"Dead-time mode = {'AUTO (preset)' if dead_mode.startswith('Auto') else 'MANUAL'}"
        )
        lines.append(f"Dead time Δt = {dead_time:.3f} s\n")

        lines.append("=== STEP 1 — Slant range to outer beam ===")
        lines.append("R = D / cos(θ)")
        lines.append(f"  = {depth:.3f} / cos({theta_deg:.3f}°)")
        lines.append(f"  = {R:.3f} m\n")

        lines.append("=== STEP 2 — Two-way travel time & ping cycle ===")
        lines.append("t_2way = 2R / c")
        lines.append(f"       = 2×{R:.3f} / {sound_speed:.3f}")
        lines.append(f"       = {t_2way:.3f} s")
        lines.append("T = t_2way + Δt")
        lines.append(f"  = {t_2way:.3f} + {dead_time:.3f}")
        lines.append(f"  = {T:.3f} s\n")

        lines.append("=== STEP 3 — Along-track footprint length ===")
        lines.append("L = 2D · tan(φ_T / 2)")
        lines.append(f"  = 2×{depth:.3f}×tan({beam_deg/2:.3f}°)")
        lines.append(f"  = {L:.3f} m\n")

        lines.append("=== STEP 4 — Allowed advance per ping ===")
        lines.append("d = (advance fraction) × L")
        lines.append(f"  = {advance_fraction:.3f} × {L:.3f}")
        lines.append(f"  = {d:.3f} m\n")

        lines.append("=== STEP 5 — Maximum vessel speed ===")
        lines.append("v = d / T")
        lines.append(f"  = {d:.3f} / {T:.3f}")
        lines.append(f"  = {v_ms:.3f} m/s")
        lines.append(f"  = {v_knots:.3f} knots\n")

        lines.append("FINAL RESULT:")
        lines.append(f"Max vessel speed ≈ {v_ms:.3f} m/s ≈ {v_knots:.2f} knots")

        st.text("\n".join(lines))

    except ValueError as e:
        st.error(f"Input error: {e}")
