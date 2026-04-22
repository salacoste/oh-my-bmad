pub mod default;

use crate::Result;
use crate::events::{IncomingEvent, MessageFormat};

pub use default::DefaultRenderer;

pub trait Renderer: Send + Sync {
    fn render(&self, event: &IncomingEvent, format: &MessageFormat) -> Result<String>;
}
