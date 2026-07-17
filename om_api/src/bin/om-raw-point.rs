use anyhow::{Context, Result};
use chrono::{DateTime, Utc};
use clap::Parser;
use om_api::official::OfficialDecoder;
use om_api::query::read_raw_product_point;
use om_api::snapshot::OmDataSnapshot;
use std::path::PathBuf;

#[derive(Debug, Parser)]
#[command(
    version,
    about = "Decode an exact stored OM point without API interpolation or fallback"
)]
struct Args {
    #[arg(long)]
    data_root: PathBuf,
    #[arg(long)]
    omfile_lib: PathBuf,
    #[arg(long)]
    product: String,
    #[arg(long)]
    variable: String,
    #[arg(long)]
    valid_time: String,
    #[arg(long)]
    source_run: Option<String>,
    #[arg(long)]
    latitude: f64,
    #[arg(long)]
    longitude: f64,
}

fn main() -> Result<()> {
    let args = Args::parse();
    let valid_time_utc = DateTime::parse_from_rfc3339(&args.valid_time)
        .with_context(|| format!("invalid --valid-time: {}", args.valid_time))?
        .with_timezone(&Utc);
    let snapshot = OmDataSnapshot::load(&args.data_root)?;
    let decoder = OfficialDecoder::load(&args.omfile_lib)?;
    let point = read_raw_product_point(
        &snapshot,
        Some(&decoder),
        &args.product,
        &args.variable,
        valid_time_utc,
        args.source_run.as_deref(),
        args.latitude,
        args.longitude,
    )?;
    println!("{}", serde_json::to_string(&point)?);
    Ok(())
}
